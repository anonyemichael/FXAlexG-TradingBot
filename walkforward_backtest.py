"""
Walk-forward backtester that replays the LIVE strategy.analyze_pair() exactly as bot.py calls it.

- No lookahead: at each decision time T the strategy only sees bars that closed before T
  plus a "forming" bar (open=high=low=close=last close), exactly like a live scan right
  after a candle close. D1/W1 forming bars are built from the 4H bars of the current day/week.
- Same window lengths as live (W1 100, D1 200, H4 200, M15 200) so indicator warm-up matches.
- Same scan schedule: every 15 min inside kill zones, 4H closes otherwise.
- Same trade management: hard SL/TP2, 50% partial at TP1 + SL to breakeven buffer,
  15M EMA21 trailing after TP1, max-lifespan close, 24h cooldown after a loss, setup dedup.
- Costs: typical spread from config.NORMAL_SPREAD_POINTS (bars are bid prices).
- Results are in R (multiples of the initial risk) so they are independent of account size.

Usage (needs JSON bar dumps from the bridge /ohlcv endpoint in DATA_DIR, named SYMBOL_TF.json):
    python3 walkforward_backtest.py DATA_DIR [SYMBOL ...]
"""

import json
import os
import sys
from datetime import timedelta
from multiprocessing import Pool

import numpy as np
import pandas as pd

import config

# Optional overrides for experiments, e.g. BT_CFG='{"MAX_TRADE_LIFESPAN_HOURS": 120}'
for _k, _v in json.loads(os.environ.get("BT_CFG", "{}")).items():
    setattr(config, _k, _v)

import strategy  # noqa: E402  (must import after overrides: strategy copies config values at import)

LIVE_LEN = {"W1": 100, "D1": 200, "H4": 200, "M15": 200}
KZ_HOURS = set(range(0, 4)) | set(range(7, 10)) | set(range(12, 15))
CRYPTO_TAGS = ("BTC", "ETH", "SOL", "XRP", "LTC")


def _is_crypto(sym):
    return any(t in sym for t in CRYPTO_TAGS)


def _market_open(ts, sym):
    if _is_crypto(sym):
        return True
    wd, h = ts.weekday(), ts.hour
    if wd == 5 or (wd == 6 and h < 21) or (wd == 4 and h >= 21):
        return False
    return True


def _kill_zone(ts, sym):
    return _market_open(ts, "EURUSD") and ts.hour in KZ_HOURS


def load(data_dir, sym, tf):
    path = os.path.join(data_dir, f"{sym}_{tf}.json")
    if not os.path.exists(path) or os.path.getsize(path) < 100:
        return None
    raw = json.load(open(path))
    bars = raw["bars"] if isinstance(raw, dict) else raw
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float)
    df.attrs["symbol"] = sym
    return df


def _stub(ts, price):
    return pd.DataFrame({"open": [price], "high": [price], "low": [price], "close": [price], "volume": [0.0]},
                        index=pd.DatetimeIndex([ts]))


def _with_forming(df_closed, partial_bars, ts, n):
    """Closed higher-TF bars + a forming bar aggregated from lower-TF bars seen so far."""
    if partial_bars is not None and len(partial_bars):
        f = pd.DataFrame({
            "open": [partial_bars["open"].iloc[0]], "high": [partial_bars["high"].max()],
            "low": [partial_bars["low"].min()], "close": [partial_bars["close"].iloc[-1]], "volume": [0.0],
        }, index=pd.DatetimeIndex([ts]))
        out = pd.concat([df_closed.iloc[-(n - 1):], f])
    else:
        out = df_closed.iloc[-n:]
    return out


def spread_price(sym):
    pts = config.NORMAL_SPREAD_POINTS.get(sym, 20)
    if "JPY" in sym:
        point = 0.001
    elif "XAU" in sym:
        point = 0.001
    elif any(t in sym for t in ("BTC", "ETH", "LTC")):
        point = 0.01
    elif any(t in sym for t in ("SOL", "XRP")):
        point = 0.0001 if "XRP" in sym else 0.001
    else:
        point = 0.00001
    return pts * point


def be_buffer(sym):
    if "BTC" in sym: return 15.0
    if "ETH" in sym: return 1.0
    if "XAU" in sym: return 1.0
    if "JPY" in sym: return 0.04
    return 0.0004


def simulate_symbol(args):
    data_dir, sym = args
    w = load(data_dir, sym, "W1")
    d = load(data_dir, sym, "D1")
    h4 = load(data_dir, sym, "H4")
    m15 = load(data_dir, sym, "M15")
    if d is None or h4 is None:
        return sym, []
    if w is None:
        w = h4.resample("W-SUN", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()

    spread = spread_price(sym)
    pip = 0.01 if "JPY" in sym else (1.0 if any(t in sym for t in ("XAU", "BTC")) else (0.1 if "ETH" in sym else 0.0001))
    m15_start = m15.index[0] + timedelta(hours=60) if m15 is not None else None

    # Decision times: every H4 close; plus every M15 close inside kill zones where 15M data exists.
    start = h4.index[0] + timedelta(days=60)
    times = [t for t in h4.index if t >= start]
    if m15 is not None:
        kz_times = [t + timedelta(minutes=15) for t in m15.index if t >= m15_start]
        kz_times = [t for t in kz_times if _kill_zone(t, sym)]
        times = sorted(set(times) | set(kz_times))

    # Price path for trade management: M15 when available, H4 otherwise.
    path_h4 = h4[h4.index < (m15_start if m15 is not None else h4.index[-1] + timedelta(days=1))]
    path = pd.concat([path_h4, m15[m15.index >= m15_start]]) if m15 is not None else h4
    if m15 is not None:
        m15i = strategy.add_indicators(m15)
    path_idx = path.index

    trades = []
    pos = None
    last_sig = None
    last_loss_at = None
    path_ptr = 0

    def step_until(ts):
        """Advance open position through price bars that close at or before ts."""
        nonlocal pos, path_ptr, last_loss_at
        while path_ptr < len(path) and pos is not None:
            bt = path_idx[path_ptr]
            bar_len = timedelta(minutes=15) if (m15 is not None and bt >= m15_start) else timedelta(hours=4)
            if bt + bar_len > ts:
                break
            if bt < pos["open_time"]:
                path_ptr += 1
                continue
            b = path.iloc[path_ptr]
            hi, lo = b["high"], b["low"]
            buy = pos["dir"] == "bullish"
            # Bid-chart: buys exit at bid, sells exit at ask (= bid + spread)
            ex_hi, ex_lo = (hi, lo) if buy else (hi + spread, lo + spread)
            sl_hit = (ex_lo <= pos["sl"]) if buy else (ex_hi >= pos["sl"])
            tp1_hit = (ex_hi >= pos["tp1"]) if buy else (ex_lo <= pos["tp1"])
            tp2_hit = (ex_hi >= pos["tp2"]) if buy else (ex_lo <= pos["tp2"])
            sgn = 1 if buy else -1

            def r_at(px):
                return sgn * (px - pos["fill"]) / pos["risk"]

            if sl_hit:  # conservative: SL first if both inside the same bar
                pos["r"] += pos["rem"] * r_at(pos["sl"])
                _close(bt, "SL" if not pos["partial"] else "BE/trail")
                continue
            if not pos["partial"] and tp1_hit and pos["use_partial"]:
                pos["r"] += config.PARTIAL_PCT * r_at(pos["tp1"])
                pos["rem"] = 1 - config.PARTIAL_PCT
                pos["partial"] = True
                pos["sl"] = pos["entry_ref"] + sgn * be_buffer(sym)
            if tp2_hit:
                pos["r"] += pos["rem"] * r_at(pos["tp2"])
                _close(bt, "TP2")
                continue
            # trailing after TP1 on 15M EMA21 (only where 15M data exists)
            if pos["partial"] and config.USE_TRAILING_STOP and m15 is not None and bt >= m15_start:
                k = m15i.index.get_indexer([bt])[0]
                if k > 1:
                    ema = float(m15i["ema21"].iloc[k - 1])
                    buf = config.TRAILING_STOP_BUFFER * pip
                    trail = ema - buf if buy else ema + buf
                    if buy and trail > pos["sl"] and trail > pos["fill"]:
                        pos["sl"] = trail
                    if not buy and trail < pos["sl"] and trail < pos["fill"]:
                        pos["sl"] = trail
            # lifespan
            ttl = getattr(config, "MAX_TRADE_LIFESPAN_HOURS", 0)
            if ttl and not pos["partial"] and (bt + bar_len - pos["open_time"]) > timedelta(hours=ttl) \
                    and _market_open(bt + bar_len, sym):
                exit_px = b["close"] if buy else b["close"] + spread
                pos["r"] += pos["rem"] * r_at(exit_px)
                _close(bt + bar_len, "TTL")
                continue
            path_ptr += 1

    def _close(ts, why):
        nonlocal pos, last_loss_at
        pos.update({"close_time": ts, "exit": why})
        if pos["r"] < 0:
            last_loss_at = ts
        trades.append({k: pos[k] for k in ("symbol", "trigger", "dir", "open_time", "close_time", "exit", "r", "grade", "score", "risk_pts")})
        pos = None

    for T in times:
        if pos is not None:
            step_until(T)
        if pos is not None:
            continue
        if not _market_open(T, sym):
            continue
        if last_loss_at is not None and (T - last_loss_at) < timedelta(hours=config.COOLDOWN_HOURS):
            continue
        kz = _kill_zone(T, sym)
        is_h4_close = (T.minute == 0 and T.hour % 4 == 0)
        if not kz and not is_h4_close:
            continue

        h4_closed = h4[h4.index + timedelta(hours=4) <= T]
        if len(h4_closed) < 60:
            continue
        last_close = float(h4_closed["close"].iloc[-1])
        # forming H4 bar from M15 bars inside it (if any), else stub
        cur_h4_open = T.floor("4h")
        part_15 = m15[(m15.index >= cur_h4_open) & (m15.index + timedelta(minutes=15) <= T)] if m15 is not None else None
        df4 = _with_forming(h4_closed, part_15 if part_15 is not None and len(part_15) else _stub(T, last_close), T, LIVE_LEN["H4"])
        day_open = T.floor("1D")
        d_closed = d[d.index + timedelta(days=1) <= T]
        dfd = _with_forming(d_closed, h4[(h4.index >= day_open) & (h4.index + timedelta(hours=4) <= T)], T, LIVE_LEN["D1"])
        wk_open = (T - timedelta(days=(T.weekday() + 1) % 7)).floor("1D")
        w_closed = w[w.index < wk_open]
        dfw = _with_forming(w_closed, h4[(h4.index >= wk_open) & (h4.index + timedelta(hours=4) <= T)], T, LIVE_LEN["W1"])
        df15 = None
        if m15 is not None and T >= m15_start:
            m_closed = m15[m15.index + timedelta(minutes=15) <= T]
            df15 = pd.concat([m_closed.iloc[-(LIVE_LEN["M15"] - 1):], _stub(T, float(m_closed["close"].iloc[-1]))])
            df15.attrs["symbol"] = sym
        for df_ in (df4, dfd, dfw):
            df_.attrs["symbol"] = sym

        try:
            setup = strategy.analyze_pair(sym, dfw, dfd, df4, df_15m=df15, in_kill_zone=kz and df15 is not None)
        except Exception as e:
            print(f"[{sym}] analyze error at {T}: {e}")
            continue
        if not setup:
            continue
        grade_ok = setup["grade"] == config.MIN_GRADE or (
            getattr(config, "AI_UPGRADE_B_PLUS", False) and setup["grade"] == "B+" and setup["score"] >= 80
            and getattr(config, "BT_ASSUME_AI_UPGRADES", False))
        if not grade_ok:
            continue
        key = (setup["direction"], round(setup.get("swing_eq", 0), 3))
        if key == last_sig:
            continue
        last_sig = key

        # fill at next bar open (≈ current price right after the close)
        nxt = path.index.searchsorted(T)
        if nxt >= len(path):
            break
        px = float(path["open"].iloc[nxt])
        buy = setup["direction"] == "bullish"
        fill = px + spread if buy else px
        risk = (fill - setup["sl"]) if buy else (setup["sl"] - fill)
        if risk <= 0:
            continue
        if getattr(config, "REVALIDATE_RR_AT_FILL", False):
            reward = (setup["tp2"] - fill) if buy else (fill - setup["tp2"])
            if reward / risk < config.MIN_RR:
                continue
        pos = {
            "symbol": sym, "trigger": setup["trigger"], "dir": setup["direction"], "open_time": path.index[nxt],
            "fill": fill, "entry_ref": setup["entry"], "sl": setup["sl"], "tp1": setup["tp1"], "tp2": setup["tp2"],
            "risk": risk, "risk_pts": risk, "r": 0.0, "rem": 1.0, "partial": False,
            "use_partial": True, "grade": setup["grade"], "score": setup["score"],
        }
        path_ptr = nxt
    return sym, trades


def summarize(df, label):
    if df.empty:
        print(f"{label}: no trades")
        return
    wins = df[df.r > 0]
    losses = df[df.r <= 0]
    pf = wins.r.sum() / max(-losses.r.sum(), 1e-9)
    eq = df.sort_values("close_time").r.cumsum()
    dd = (eq.cummax() - eq).max()
    print(f"{label:<28} n={len(df):4d}  win={len(wins)/len(df)*100:5.1f}%  avgR={df.r.mean():+.3f}  "
          f"totR={df.r.sum():+7.2f}  PF={pf:4.2f}  maxDD={dd:5.2f}R")


if __name__ == "__main__":
    data_dir = sys.argv[1]
    syms = sys.argv[2:] or config.SYMBOLS
    workers = int(os.environ.get("BT_WORKERS", "3"))  # each worker holds a symbol's full history in RAM
    with Pool(min(len(syms), workers), maxtasksperchild=1) as pool:
        results = pool.map(simulate_symbol, [(data_dir, s) for s in syms])
    rows = [t for _, ts in results for t in ts]
    df = pd.DataFrame(rows)
    out = os.environ.get("BT_OUT")
    if out:
        df.to_csv(out, index=False)
    print("=" * 100)
    summarize(df, "ALL")
    if not df.empty:
        for trig, g in df.groupby("trigger"):
            summarize(g, f"trigger={trig}")
        for s, g in df.groupby("symbol"):
            summarize(g, f"  {s}")
        df = df.sort_values("open_time")
        cut = df.open_time.iloc[int(len(df) * 0.6)] if len(df) > 5 else None
        if cut is not None:
            summarize(df[df.open_time < cut], "first 60% (in-sample)")
            summarize(df[df.open_time >= cut], "last 40% (out-of-sample)")
        print(df.exit.value_counts().to_string())
