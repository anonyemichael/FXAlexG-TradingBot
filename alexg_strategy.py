"""
FXAlexG "Set & Forget" swing strategy, as a mechanical rule set.

Alex G's method has three steps: trend, area of interest (AOI), entry. This module codes each step
with fixed rules so the bot and the backtest (alexg_research.py) use the same code:

  1. TREND   Market structure from candle CLOSES (bodies, not wicks) on W1, D1 and the 4H.
             A close above the last confirmed swing high = bullish; below the last swing low = bearish.
             At least `min_sync` of the three timeframes must agree with the trade (D1 always must).
  2. AOI     A horizontal zone where daily price has turned at least `aoi_min_touches` times
             (daily swing highs AND lows, so old resistance can become support). The pullback must
             reach the zone within the last `pullback_bars` entry candles, and the entry candle must
             close back on the right side of it.
  3. ENTRY   On the entry timeframe (4H by default, 1H for the faster version):
               - shift of structure: the candle closes beyond the last minor swing (the pullback's
                 lower high for a buy / higher low for a sell), and
               - engulfing: the same candle closes beyond the previous candle's body.
             Stop loss beyond the pullback's extreme (+ a small ATR buffer).
             Take profit at a fixed `rr` multiple of the risk (Alex's minimum is 1:2.5).
             Set & forget: no partials, no breakeven, no trailing.

Every value is known at the close of the signal candle (no lookahead): higher-timeframe trends and AOIs
come only from W1/D1 bars that had already closed before the signal candle opened.
"""

import numpy as np
import pandas as pd

TRIGGER = "alexg_sf"            # stored with each trade (trades.trigger)
TRIGGER_FAST = "alexg_sf_1h"

DEFAULTS = {
    "trend_k_htf": 3,        # swing = close higher/lower than k closes on each side (W1, D1)
    "trend_k_entry": 5,      # same for the 4H trend (third timeframe)
    "sos_k": 2,              # minor swing window for the shift of structure
    "sos_lookback": 12,      # the swing being broken must be at most this many candles old
    "min_sync": 3,           # of W1 / D1 / 4H, how many must agree (D1 is always required)
    "aoi_lookback_days": 250,
    "aoi_pivot_k": 2,        # daily wick pivots used to build AOIs
    "aoi_tol_atr": 0.35,     # half-width of an AOI as a fraction of daily ATR(14)
    "aoi_min_touches": 2,
    "pullback_bars": 6,      # the AOI touch must be within the last N entry candles
    "engulf": "body",        # "body": close beyond prev body; "strict": prev candle opposite colour too
    "rr": 2.5,
    "sl_buffer_atr": 0.15,   # extra room beyond the pullback extreme, in entry-TF ATR(14)
    "min_sl_atr": 0.8,       # stop is widened to at least this many entry-TF ATRs
    "max_sl_atr": 3.0,       # setups needing a wider stop are skipped
    "longs": True,
    "shorts": True,
    "trend_mode": "structure",   # "structure" (swing closes) or "ema" (close vs EMA on each TF)
    "trend_ema": 50,
    "need_w": False,         # require the weekly trend specifically (not just any 2 of 3)
    "ema_touch": False,      # pullback must also tag the entry-TF 50 EMA (Alex's EMA confluence)
    "use_aoi": True,
    "aoi_mode": "any",       # "any": >= N swing points; "flip": zone holds both swing highs AND lows (S/R flip)
    "regime": None,          # e.g. [20, 100]: only buy while daily EMA20 > EMA100 (sell while below)
}

TF_HOURS = {"H1": 1, "H4": 4}


# ── helpers ──────────────────────────────────────────────────────────────────

def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, min_periods=n).mean()


def resample(df, rule):
    """OHLC bars aggregated to `rule` ('1D', 'W'), labelled by period START. Empty periods dropped."""
    if rule == "W":
        r = df.resample("W-SUN", label="left", closed="left")
    else:
        r = df.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                        "low": r["low"].min(), "close": r["close"].last()})
    return out.dropna()


def _pivots(values, k, kind):
    """is_pivot[j] True if values[j] is the max (kind='high') / min ('low') of the 2k+1 window."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    out = np.zeros(n, dtype=bool)
    for j in range(k, n - k):
        w = v[j - k:j + k + 1]
        if kind == "high" and v[j] == w.max() and (w == v[j]).sum() == 1:
            out[j] = True
        elif kind == "low" and v[j] == w.min() and (w == v[j]).sum() == 1:
            out[j] = True
    return out


def structure_state(close, k):
    """+1 / -1 / 0 per bar, from swing points on closes confirmed k bars later.
    Bullish after a close above the last swing-high close; bearish after a close below the last swing-low."""
    c = np.asarray(close, dtype=float)
    ph, pl = _pivots(c, k, "high"), _pivots(c, k, "low")
    state = np.zeros(len(c), dtype=int)
    last_hi = last_lo = None
    s = 0
    for i in range(len(c)):
        j = i - k                       # pivot at j is confirmed once bar i has closed
        if j >= 0:
            if ph[j]:
                last_hi = c[j]
            if pl[j]:
                last_lo = c[j]
        if last_hi is not None and c[i] > last_hi:
            s = 1
        elif last_lo is not None and c[i] < last_lo:
            s = -1
        state[i] = s
    return state


def _htf_on_entry(entry_index, htf, values):
    """Value of a higher-timeframe series as of the last HTF bar that CLOSED before each entry bar opened."""
    s = pd.Series(values, index=htf.index).shift(1)    # value at period start = previous period's close
    return s.reindex(entry_index, method="ffill").to_numpy()


def _engulf(o, c, i, direction, mode):
    if direction > 0:
        ok = c[i] > o[i] and c[i] > max(o[i - 1], c[i - 1])
        if mode == "strict":
            ok = ok and c[i - 1] < o[i - 1]
    else:
        ok = c[i] < o[i] and c[i] < min(o[i - 1], c[i - 1])
        if mode == "strict":
            ok = ok and c[i - 1] > o[i - 1]
    return ok


def _aoi(piv_t, piv_v, piv_k, now, look, p, d_atr, ext, close, direction):
    """Daily AOI touched by the pullback extreme `ext`: a level with >= aoi_min_touches daily swing
    points within +/- tol, that the signal candle closed beyond (support under a buy, resistance over a sell)."""
    known = (piv_t <= now) & (piv_t >= now - look)
    levels, kinds = piv_v[known], piv_k[known]
    if len(levels) < p["aoi_min_touches"]:
        return None
    tol = p["aoi_tol_atr"] * d_atr
    for L in levels[np.abs(levels - ext) <= tol]:
        m = np.abs(levels - L) <= tol
        if m.sum() < p["aoi_min_touches"]:
            continue
        if p["aoi_mode"] == "flip" and not ((kinds[m] == 1).any() and (kinds[m] == -1).any()):
            continue
        if (direction > 0 and close > L) or (direction < 0 and close < L):
            return float(L)
    return None


# ── main signal generator ────────────────────────────────────────────────────

def compute_signals(df, params=None, only_last=False):
    """
    df: entry-timeframe OHLC bars (UTC DatetimeIndex, CLOSED bars only), several hundred days long
        so the W1/D1 trend and AOIs can be built from it.
    Returns a list of dicts {i, time, direction (+1/-1), entry (signal close), sl, tp, risk, ...}.
    only_last=True evaluates just the last bar (live use).
    """
    p = {**DEFAULTS, **(params or {})}
    if df is None or len(df) < 200:
        return []
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    a = atr(df).to_numpy()

    d1 = resample(df, "1D")
    w1 = resample(df, "W")
    if p["trend_mode"] == "ema":
        # trend = close vs EMA(trend_ema) on each timeframe (Alex's 50 EMA), instead of swing structure
        n_e = p["trend_ema"]
        def _ema_state(x):
            e = x.ewm(span=n_e, adjust=False).mean()
            return np.sign(x - e).astype(int).to_numpy()
        d_state = _htf_on_entry(df.index, d1, _ema_state(d1["close"]))
        w_state = _htf_on_entry(df.index, w1, _ema_state(w1["close"]))
        e_state = _ema_state(df["close"])
    else:
        d_state = _htf_on_entry(df.index, d1, structure_state(d1["close"], p["trend_k_htf"]))
        w_state = _htf_on_entry(df.index, w1, structure_state(w1["close"], p["trend_k_htf"]))
        e_state = structure_state(c, p["trend_k_entry"])
    d_atr = _htf_on_entry(df.index, d1, atr(d1).to_numpy())
    ema_e = df["close"].ewm(span=50, adjust=False).mean().to_numpy()
    if p["regime"]:
        f, sl_ = p["regime"]
        ef = d1["close"].ewm(span=f, adjust=False).mean()
        es = d1["close"].ewm(span=sl_, adjust=False).mean()
        regime = _htf_on_entry(df.index, d1, np.sign(ef - es).astype(int).to_numpy())
    else:
        regime = None

    # daily wick pivots for AOIs: (time the pivot became known, level)
    kp = p["aoi_pivot_k"]
    dh, dl = d1["high"].to_numpy(), d1["low"].to_numpy()
    piv_t, piv_v, piv_k = [], [], []
    for j in np.where(_pivots(dh, kp, "high"))[0]:
        if j + kp < len(d1):
            piv_t.append(d1.index[j + kp] + pd.Timedelta(days=1)); piv_v.append(dh[j]); piv_k.append(1)
    for j in np.where(_pivots(dl, kp, "low"))[0]:
        if j + kp < len(d1):
            piv_t.append(d1.index[j + kp] + pd.Timedelta(days=1)); piv_v.append(dl[j]); piv_k.append(-1)
    piv_t = pd.DatetimeIndex(piv_t).tz_localize(None) if piv_t and pd.DatetimeIndex(piv_t).tz is not None \
        else pd.DatetimeIndex(piv_t)
    order = np.argsort(piv_t.values)
    piv_t = piv_t.values[order].astype("datetime64[ns]")
    piv_v = np.array(piv_v, dtype=float)[order]
    piv_k = np.array(piv_k, dtype=int)[order]

    # minor swings on closes for the shift of structure
    sk = p["sos_k"]
    sh_minor, sl_minor = _pivots(c, sk, "high"), _pivots(c, sk, "low")

    ix = df.index.tz_localize(None) if df.index.tz is not None else df.index
    idx_ns = ix.values.astype("datetime64[ns]")
    look = np.timedelta64(int(p["aoi_lookback_days"]), "D")
    start = max(60, sk * 2 + 2)
    rng = [len(df) - 1] if only_last else range(start, len(df))
    out = []
    for i in rng:
        if i < start or np.isnan(a[i]) or np.isnan(d_atr[i]):
            continue
        for direction in (1, -1):
            if direction > 0 and not p["longs"]:
                continue
            if direction < 0 and not p["shorts"]:
                continue
            # 1. trend: D1 must agree, plus enough of W1 / entry-TF
            if regime is not None and regime[i] != direction:
                continue
            if d_state[i] != direction and p["min_sync"] > 0:
                continue
            sync = 1 + int(w_state[i] == direction) + int(e_state[i] == direction)
            if sync < p["min_sync"] or (p["need_w"] and w_state[i] != direction):
                continue
            # 3a. engulfing + shift of structure on this candle
            if not _engulf(o, c, i, direction, p["engulf"]):
                continue
            lvl = None
            for j in range(i - sk - 1, max(i - p["sos_lookback"], sk) - 1, -1):  # newest confirmed swing
                if direction > 0 and sh_minor[j]:
                    lvl = c[j]; break
                if direction < 0 and sl_minor[j]:
                    lvl = c[j]; break
            if lvl is None:
                continue
            if direction > 0 and not (c[i] > lvl >= c[i - 1]):
                continue
            if direction < 0 and not (c[i] < lvl <= c[i - 1]):
                continue
            # 2. AOI: pullback extreme touched a daily zone with >= N touches, candle closed beyond it
            pb = slice(max(0, i - p["pullback_bars"] + 1), i + 1)
            ext = l[pb].min() if direction > 0 else h[pb].max()
            if p["ema_touch"]:
                e_pb = ema_e[pb]
                if (direction > 0 and not (l[pb] <= e_pb).any()) or (direction < 0 and not (h[pb] >= e_pb).any()):
                    continue
            if not p["use_aoi"]:
                zone = float(ext)
            else:
                zone = _aoi(piv_t, piv_v, piv_k, idx_ns[i], look, p, d_atr[i], ext, c[i], direction)
            if zone is None:
                continue
            # 3b. stop and target
            buf = p["sl_buffer_atr"] * a[i]
            sl = ext - buf if direction > 0 else ext + buf
            risk = (c[i] - sl) * direction
            if risk < p["min_sl_atr"] * a[i]:
                risk = p["min_sl_atr"] * a[i]
                sl = c[i] - direction * risk
            if risk > p["max_sl_atr"] * a[i]:
                continue
            out.append({
                "i": i, "time": df.index[i], "direction": direction, "entry": float(c[i]),
                "sl": float(sl), "tp": float(c[i] + direction * p["rr"] * risk), "risk": float(risk),
                "atr": float(a[i]), "aoi": zone, "sos_level": float(lvl), "sync": sync,
                "w": int(w_state[i]), "d": int(d_state[i]), "e": int(e_state[i]),
            })
    return out


def closed_bars(df, tf_hours=4, now=None):
    """Only candles that have fully closed (the bridge's last row is usually still forming)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    return df[(idx + pd.Timedelta(hours=tf_hours)) <= now]


def entry_signal(symbol, df, params=None, trigger=TRIGGER, now=None, tf_hours=4):
    """Live: df = entry-TF bars from the bridge (a forming last candle is dropped by time).
    Returns a setup dict in the format bot.py / trade_logger expect, or None."""
    if df is None or len(df) < 300:
        return None
    closed = closed_bars(df, tf_hours, now)
    sigs = compute_signals(closed, params, only_last=True)
    if not sigs:
        return None
    s = sigs[-1]
    p = {**DEFAULTS, **(params or {})}
    bull = s["direction"] > 0
    names = {1: "bullish", -1: "bearish", 0: "neutral"}
    return {
        "symbol": symbol, "direction": "bullish" if bull else "bearish",
        "entry": s["entry"], "sl": s["sl"], "sl_dist": s["risk"],
        "tp": s["tp"], "tp1": None, "tp2": s["tp"], "rr": p["rr"],
        "grade": "SF", "score": 100,
        "met": [f"D1 trend {'up' if bull else 'down'} ({s['sync']}/3 TFs in sync)",
                f"Pullback into daily AOI {s['aoi']:.5g}",
                f"Shift of structure through {s['sos_level']:.5g} + engulfing close",
                f"Stop beyond pullback, target 1:{p['rr']}"],
        "trigger": trigger, "atr": s["atr"],
        "w_bias": names[s["w"]], "d_trend": names[s["d"]], "h4_bias": names[s["e"]],
        "synced": s["sync"], "swing_eq": round(s["aoi"], 5), "adx": 0.0,
        "signal_time": str(s["time"]),
    }
