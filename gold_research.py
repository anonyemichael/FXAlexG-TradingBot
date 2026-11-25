"""
Gold + forex research, both directions (results: GOLD_FOREX_RESEARCH.md).

Data (free, public, broker server time GMT+2/+3), put under FX_DATA (default ./data):
  ejt/  github.com/ejtraderLabs/historical-data   11 FX pairs + XAUUSD, D1/H4/H1, 2012-11 .. 2022-03
  ts4/  github.com/ts4blader/market_data          EURUSD GBPUSD AUDUSD USDCAD USDCHF XAUUSD D1/H4/H1 2020-2025
  mt5/  github.com/13otKmdr/MT5-AlgoLab            28 FX pairs + XAUUSD + XAGUSD H4, 2024-01 .. 2026-01
Segments: design = entries before 2020 (ejt); oos = 2020 onward (ts4 spliced after ejt where available);
unseen = 16 pairs + silver never used in design (mt5).
Costs: conservative Exness Standard spreads per round trip, swap 2%/yr of notional (4% metals), fills at the
next candle's open, gaps through the stop fill at the open, stop first when a candle touches stop and target.

Usage:  FX_DATA=path python3 gold_research.py families   # every family, every segment (or: donchian ema rsi bb london)
        FX_DATA=path python3 gold_research.py gold       # the live gold rule (metals_strategy.py)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
FAM = sys.argv[1] if len(sys.argv) > 1 else "gold"
FAM = "all" if FAM == "families" else FAM   # or one of: donchian ema rsi bb london

D = os.environ.get("FX_DATA", "data")
EJT = os.path.join(D, "ejt")
TS4 = os.path.join(D, "ts4")
MT5 = os.path.join(D, "mt5")

DESIGN = ["EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
          "EURGBP", "EURCHF", "XAUUSD"]
TS4_SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF", "XAUUSD"]
UNSEEN = ["AUDCAD", "AUDCHF", "AUDNZD", "CADCHF", "CADJPY", "CHFJPY", "EURAUD", "EURCAD", "EURNZD",
          "GBPAUD", "GBPCAD", "GBPCHF", "NZDCAD", "NZDCHF", "NZDUSD", "XAGUSD"]
SPLIT = pd.Timestamp("2020-01-01")


def spread(sym):
    if sym == "XAUUSD":
        return 0.30
    if sym == "XAGUSD":
        return 0.03
    jpy = sym.endswith("JPY")
    if sym in ("EURUSD",):
        s = 1.2
    elif sym in ("GBPUSD", "AUDUSD", "USDJPY"):
        s = 1.5
    elif sym in ("USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURCHF", "EURJPY"):
        s = 2.0
    else:
        s = 3.0
    return s * (0.01 if jpy else 0.0001)


SWAP_YR = {"XAUUSD": 0.04, "XAGUSD": 0.04}


def _ejt(sym, tf):
    df = pd.read_csv(os.path.join(EJT, sym, f"{sym}{tf.lower()}.csv"))
    scale = 100.0 if sym == "XAUUSD" else 1000.0 if sym.endswith("JPY") else 100000.0
    df.index = pd.to_datetime(df["Date"])
    return df[["open", "high", "low", "close"]].astype(float) / scale


def _ts4(sym, tf):
    df = pd.read_csv(os.path.join(TS4, f"{sym}_{tf}.csv"))
    df.index = pd.to_datetime(df["Datetime"].astype(str).str[:19])
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close"]].astype(float)


def _mt5(sym):
    df = pd.read_csv(os.path.join(MT5, f"{sym}_H4.csv"))
    df.index = pd.to_datetime(df["time"].astype(str).str[:19])
    return df[["open", "high", "low", "close"]].astype(float)


def to_d1(h):
    return h.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


_cache = {}


def load(sym, tf, source="main"):
    """source 'main': ejt (+ ts4 from 2020 where available). 'mt5': 2024-2026 H4 (D1 resampled)."""
    key = (sym, tf, source)
    if key in _cache:
        return _cache[key]
    if source == "mt5":
        h4 = _mt5(sym)
        df = h4 if tf == "H4" else to_d1(h4)
    else:
        df = _ejt(sym, tf)
        if sym in TS4_SYMS:
            df = pd.concat([df[df.index < SPLIT], _ts4(sym, tf)[lambda x: x.index >= SPLIT]])
    df = df[~df.index.duplicated()].sort_index()
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    _cache[key] = df
    return df


# ── indicators ────────────────────────────────────────────────────────────────
def atr(df, n=20):
    pc = df["close"].shift()
    tr = np.maximum(df["high"] - df["low"], np.maximum((df["high"] - pc).abs(), (df["low"] - pc).abs()))
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(pdm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    ndi = 100 * pd.Series(ndm, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def d1_on(df_tf, d1_series):
    """Value of a daily series known at each intraday bar (yesterday's close value)."""
    s = d1_series.shift(1)
    s.index = s.index.normalize()
    return s.reindex(df_tf.index.normalize(), method="ffill").values


# ── simulator ─────────────────────────────────────────────────────────────────
def simulate(df, sym, long_sig, short_sig, stop_dist, trail=None, exit_long=None, exit_short=None,
             tp_r=None, max_bars=None, trail_src="close", start=None, end=None, eod_hours=None):
    """Signals are evaluated on bar close (arrays aligned with df); entries at next bar open.
    stop_dist: price distance of the initial stop (array). trail: array of trailing distance (chandelier
    from the best close/high since entry) or None. exit_*: bool arrays -> exit at next open.
    tp_r: target in R. max_bars: time stop. eod_hours: exit at the open of the first bar whose hour is in it.
    Returns list of trades (dicts with R net of costs)."""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    idx = df.index
    n = len(df)
    sp = spread(sym)
    swap_bar = SWAP_YR.get(sym, 0.02) / 365.0
    hours = idx.hour if eod_hours is not None else None
    trades = []
    pos = 0
    i = 0
    lo_t = np.searchsorted(idx.values, np.datetime64(start)) if start is not None else 0
    hi_t = np.searchsorted(idx.values, np.datetime64(end)) if end is not None else n
    while i < n - 1:
        if pos == 0:
            if i < lo_t or i >= hi_t:
                i += 1
                continue
            d = 1 if long_sig[i] else -1 if short_sig[i] else 0
            if d == 0 or not np.isfinite(stop_dist[i]) or stop_dist[i] <= 0:
                i += 1
                continue
            j = i + 1
            entry = o[j]
            risk = stop_dist[i]
            stop = entry - d * risk
            tp = entry + d * tp_r * risk if tp_r else None
            best = entry
            t0 = idx[j]
            k = j
            exit_px = None
            while k < n:
                if k > j and hours is not None and hours[k] in eod_hours:
                    exit_px = o[k]; break
                # gap through stop at the open
                if (d == 1 and o[k] <= stop) or (d == -1 and o[k] >= stop):
                    exit_px = o[k]; break
                if (d == 1 and l[k] <= stop) or (d == -1 and h[k] >= stop):
                    exit_px = stop; break
                if tp is not None and ((d == 1 and h[k] >= tp) or (d == -1 and l[k] <= tp)):
                    exit_px = max(tp, o[k]) if d == 1 else min(tp, o[k]); break
                # bar closed: exits and trailing
                if k + 1 >= n:
                    exit_px = c[k]; break
                if (d == 1 and exit_long is not None and exit_long[k]) or (d == -1 and exit_short is not None and exit_short[k]) \
                        or (max_bars and k - j + 1 >= max_bars):
                    exit_px = o[k + 1]; k += 1; break
                if trail is not None and np.isfinite(trail[k]):
                    ref = c[k] if trail_src == "close" else (h[k] if d == 1 else l[k])
                    best = max(best, ref) if d == 1 else min(best, ref)
                    ns = best - d * trail[k]
                    stop = max(stop, ns) if d == 1 else min(stop, ns)
                k += 1
            held_days = max((idx[min(k, n - 1)] - t0).total_seconds() / 86400.0, 0)
            cost = sp + entry * swap_bar * held_days
            r = (d * (exit_px - entry) - cost) / risk
            trades.append({"sym": sym, "t": t0, "d": d, "R": r, "days": held_days,
                           "risk_frac": risk / entry})
            i = k
            continue
        i += 1
    return trades


def stats(trades, weeks=None):
    if not trades:
        return {"n": 0}
    R = np.array([t["R"] for t in trades])
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    eq = np.cumsum(R[np.argsort([t["t"] for t in trades], kind="stable")])
    dd = np.max(np.maximum.accumulate(np.r_[0, eq])[1:] - eq) if len(eq) else 0
    out = {"n": len(R), "win": round(float((R > 0).mean() * 100), 1), "pf": round(pos / neg, 2) if neg > 0 else 99,
           "avgR": round(float(R.mean()), 3), "totR": round(float(R.sum()), 1), "ddR": round(float(dd), 1),
           "long_pf": None, "short_pf": None}
    for side, key in ((1, "long_pf"), (-1, "short_pf")):
        r = np.array([t["R"] for t in trades if t["d"] == side])
        if len(r):
            p, q = r[r > 0].sum(), -r[r < 0].sum()
            out[key] = round(p / q, 2) if q > 0 else 99
    if weeks:
        out["per_wk"] = round(len(R) / weeks, 2)
    return out


# ── strategy families (forex + gold) ──


def trend_flag(df, tf, filt):
    """+1 where price is above EMA(filt) on the daily chart (known at bar time), -1 below, 0 if no filter."""
    if not filt:
        return np.zeros(len(df))
    if tf == "D1":
        e = ema(df["close"], filt)
        return np.sign((df["close"] - e).values)
    d1 = to_d1(df) if tf != "D1" else df
    e = ema(d1["close"], filt)
    return np.sign(d1_on(df, d1["close"] - e))


def donchian(df, tf, N, k, filt=0):
    hh = df["high"].rolling(N).max().shift(1)
    ll = df["low"].rolling(N).min().shift(1)
    tr = trend_flag(df, tf, filt)
    a = atr(df, 20).values
    long = (df["close"] > hh).values & (tr >= 0)
    short = (df["close"] < ll).values & (tr <= 0)
    return dict(long_sig=long, short_sig=short, stop_dist=k * a, trail=k * a)


def emax(df, tf, f, s, k, filt=0):
    ef, es = ema(df["close"], f), ema(df["close"], s)
    up = (ef > es).values
    cross_up = up & ~np.r_[False, up[:-1]]
    cross_dn = ~up & np.r_[False, up[:-1]]
    a = atr(df, 20).values
    return dict(long_sig=cross_up, short_sig=cross_dn, stop_dist=k * a, trail=k * a, exit_long=~up, exit_short=up)


def rsimr(df, tf, n, lo, filt, k, ma, mb):
    r = rsi(df["close"], n).values
    tr = trend_flag(df, tf, filt)
    sma = df["close"].rolling(ma).mean().values
    c = df["close"].values
    a = atr(df, 20).values
    return dict(long_sig=(r < lo) & (tr > 0 if filt else True), short_sig=(r > 100 - lo) & (tr < 0 if filt else True),
                stop_dist=k * a, exit_long=c > sma, exit_short=c < sma, max_bars=mb)


def bbfade(df, tf, n, m, amax, k, mb):
    mid = df["close"].rolling(n).mean()
    sd = df["close"].rolling(n).std()
    ad = adx(df, 14).values
    c = df["close"].values
    a = atr(df, 20).values
    calm = ad < amax
    return dict(long_sig=(c < (mid - m * sd).values) & calm, short_sig=(c > (mid + m * sd).values) & calm,
                stop_dist=k * a, exit_long=c >= mid.values, exit_short=c <= mid.values, max_bars=mb)


def london(df, tf, b0, b1, w1, tp, minbox, maxbox, filt=0):
    """Box = server hours [b0,b1); first close beyond the box during [b1,w1) -> trade; stop = other side."""
    hr = df.index.hour
    day = df.index.normalize()
    inbox = (hr >= b0) & (hr < b1)
    g = pd.DataFrame({"h": np.where(inbox, df["high"], np.nan), "l": np.where(inbox, df["low"], np.nan), "day": day})
    bh = g.groupby("day")["h"].transform("max").values
    bl = g.groupby("day")["l"].transform("min").values
    d1 = to_d1(df)
    a_d = d1_on(df, atr(d1, 14))
    c = df["close"].values
    win = (hr >= b1) & (hr < w1)
    box = bh - bl
    ok = win & (box > minbox * a_d) & (box < maxbox * a_d)
    tr = trend_flag(df, "H1", filt)
    up = ok & (c > bh) & (tr >= 0)
    dn = ok & (c < bl) & (tr <= 0)
    first = pd.Series(up | dn, index=df.index).groupby(day).cumsum().values == 1
    up, dn = up & first, dn & first
    sd = np.where(up, c - bl, np.where(dn, bh - c, np.nan))
    return dict(long_sig=up, short_sig=dn, stop_dist=sd, tp_r=tp, eod_hours={22, 23, 0, 1, 2})


def run(fam_fn, tf, params, label, unseen=True):
    out = {}
    segs = [("design", "main", DESIGN, None, SPLIT), ("oos", "main", DESIGN, SPLIT, None)]
    if unseen and tf in ("D1", "H4"):
        segs.append(("unseen", "mt5", UNSEEN, "warm", None))
    for seg, src, syms, start, end in segs:
        trades, weeks = [], 0.0
        for s in syms:
            try:
                df = load(s, tf, src)
            except FileNotFoundError:
                continue
            kw = fam_fn(df, tf, *params)
            st = start
            if start == "warm":
                st = df.index[min(len(df) - 1, 210 if tf == "D1" else 300)]
            t = simulate(df, s, start=st, end=end, **kw)
            trades += t
            a = df.index[0] if st is None else max(pd.Timestamp(st), df.index[0])
            b = df.index[-1] if end is None else min(pd.Timestamp(end), df.index[-1])
            weeks = max(weeks, (b - a).days / 7)
        out[seg] = stats(trades, weeks)
    line = f"{label:38s}"
    for seg in ("design", "oos", "unseen"):
        r = out.get(seg)
        if r and r["n"]:
            line += (f" | {seg[:3]} n={r['n']:4d} {r['per_wk']:5.2f}/wk PF {r['pf']:5.2f} "
                     f"(L {r['long_pf']} S {r['short_pf']}) avgR {r['avgR']:+.2f} DD {r['ddR']:5.1f}")
        elif r is not None:
            line += f" | {seg[:3]} n=0"
    print(line, flush=True)
    return out



if FAM in ("donchian", "all"):
    for tf, Ns in (("D1", (20, 55)), ("H4", (42, 84))):
        for N in Ns:
            for k in (2.0, 3.0):
                for filt in (0, 100, 200):
                    run(donchian, tf, (N, k, filt), f"donch {tf} N{N} k{k} f{filt}")
if FAM in ("ema", "all"):
    for tf in ("D1", "H4"):
        for f, s in ((10, 50), (20, 100), (50, 200)):
            for k in (2.0, 3.0):
                run(emax, tf, (f, s, k), f"ema {tf} {f}/{s} k{k}")
if FAM in ("rsi", "all"):
    for tf in ("D1", "H4"):
        for lo in (5, 10, 15):
            for filt in (0, 200):
                for k in (2.0, 3.0):
                    run(rsimr, tf, (2, lo, filt, k, 5, 10), f"rsi2 {tf} <{lo} f{filt} k{k}")
if FAM in ("bb", "all"):
    for tf in ("H4", "D1"):
        for amax in (20, 25, 100):
            for m in (2.0, 2.5):
                for k in (1.5, 2.5):
                    run(bbfade, tf, (20, m, amax, k, 20), f"bbfade {tf} {m}sd adx<{amax} k{k}")
if FAM in ("london", "all"):
    for b0, b1 in ((2, 10), (0, 10)):
        for w1 in (13, 15):
            for tp in (1.0, 2.0, None):
                for filt in (0, 100):
                    run(london, "H1", (b0, b1, w1, tp, 0.2, 1.2, filt), f"london box{b0}-{b1} w<{w1} tp{tp} f{filt}")


def live_rule_report():
    """The exact live rule (metals_strategy.signal_arrays) on gold H1, plus exact-size growth."""
    import metals_strategy as MS
    df = load("XAUUSD", "H1")
    d1 = to_d1(df)
    tr = np.sign(d1_on(df, d1["close"] - ema(d1["close"], MS.TREND_EMA)))
    long_sig, short_sig, sd = MS.signal_arrays(df, tr)
    for seg, st, en, yrs in (("2012-2019", None, SPLIT, 7.1), ("2020-2025", SPLIT, None, 6.0)):
        t = simulate(df, "XAUUSD", long_sig, short_sig, sd, trail=sd, start=st, end=en)
        x = stats(t)
        row = (f"{seg}: n={x['n']} ({x['n'] / yrs / 52:.2f}/wk) win {x['win']}% PF {x['pf']} "
               f"(buys {x['long_pf']}, sells {x['short_pf']})")
        for risk in (1, 2, 3):
            eq = np.cumprod(1 + risk / 100 * np.array([z["R"] for z in t]))
            dd = np.max(1 - eq / np.maximum.accumulate(eq))
            row += f" | {risk}% risk: x{eq[-1]:.2f} ({(eq[-1] ** (1 / yrs) - 1) * 100:+.0f}%/yr, max DD {dd * 100:.0f}%)"
        print(row)


if FAM == "gold":
    live_rule_report()
