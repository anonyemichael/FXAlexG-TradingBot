"""
Trend-following research backtest (daily bars) for small-balance accounts.

Rule sets are textbook, fixed in advance — NOT tuned on this data:
  donchian_ls : enter on close beyond prior 20-day high/low, exit on close beyond prior 10-day low/high,
                initial stop 2 x ATR(20). Long and short.
  donchian_l  : same, long only.
  ema_ls      : EMA(20) crosses EMA(100) -> enter in that direction; exit on opposite cross or
                3 x ATR(20) chandelier stop (trailed from best close since entry). Long and short.
  ema_l       : same, long only.

Execution: signal on day-t close, fill at day t+1 open + spread. Stops checked against the daily
high/low (stop assumed filled exactly at the stop; gaps through it are taken at the open).
Costs: live spread from the bridge symbol info, plus SWAP_PCT_YEAR of notional per year while held.

Portfolio sim: start BALANCE, RISK_PCT per trade sized from the initial stop, rounded down to the
broker volume step; trade skipped if even the minimum lot would risk more than MAX_RISK_PCT.

Usage: python3 trend_research.py DATA_DIR
"""

import json
import os
import sys

import numpy as np
import pandas as pd

SYMS = os.environ.get("SYMS", "SOLUSDm,XRPUSDm,LTCUSDm,ADAUSDm,DOGEUSDm,BNBUSDm").split(",")
TF = os.environ.get("TF", "D1")
BALANCE = float(os.environ.get("BALANCE", 20))
RISK_PCT = float(os.environ.get("RISK_PCT", 1.0))
MAX_RISK_PCT = float(os.environ.get("MAX_RISK_PCT", 3.0))
MAX_OPEN = int(os.environ.get("MAX_OPEN", 4))
SWAP_PCT_YEAR = float(os.environ.get("SWAP_PCT_YEAR", 15.0))
CH_MULT = float(os.environ.get("CH_MULT", 3.0))
RULES = os.environ.get("RULES", "donchian_ls,donchian_l,ema_ls,ema_l").split(",")


def load(d, s):
    raw = json.load(open(os.path.join(d, f"{s}_{TF}.json")))
    df = pd.DataFrame(raw["bars"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time")[["open", "high", "low", "close"]].astype(float)
    info = json.load(open(os.path.join(d, f"{s}_info.json")))
    return df, info


def atr(df, n=20):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def signals(df, rule):
    """Returns entry series (+1/-1/0) and exit-condition helpers, all known at day-t close."""
    a = atr(df)
    if rule.startswith("donchian"):
        hi20 = df["high"].rolling(20).max().shift(1)
        lo20 = df["low"].rolling(20).min().shift(1)
        hi10 = df["high"].rolling(10).max().shift(1)
        lo10 = df["low"].rolling(10).min().shift(1)
        entry = np.where(df["close"] > hi20, 1, np.where(df["close"] < lo20, -1, 0))
        return pd.Series(entry, df.index), {"lo10": lo10, "hi10": hi10, "atr": a}
    e20 = df["close"].ewm(span=int(os.environ.get("EMA_FAST", 20)), adjust=False).mean()
    e100 = df["close"].ewm(span=int(os.environ.get("EMA_SLOW", 100)), adjust=False).mean()
    up = (e20 > e100) & (e20.shift(1) <= e100.shift(1))
    dn = (e20 < e100) & (e20.shift(1) >= e100.shift(1))
    entry = np.where(up, 1, np.where(dn, -1, 0))
    return pd.Series(entry, df.index), {"trend": np.sign(e20 - e100), "atr": a}


def trades_for(df, info, rule):
    longs_only = rule.endswith("_l")
    ent, h = signals(df, rule)
    point = info["tick_size"]
    spread = info.get("spread", 0) * point
    out = []
    pos = None
    idx = df.index
    for i in range(120, len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        if pos:
            d = pos["dir"]
            # stop check on this bar (bar i is after entry bar)
            if i > pos["i0"]:
                lo, hi = row["low"], row["high"] + (spread if d < 0 else 0)
                if (d > 0 and lo <= pos["stop"]) or (d < 0 and hi >= pos["stop"]):
                    px = min(row["open"], pos["stop"]) if d > 0 else max(row["open"] + spread, pos["stop"])
                    pos["exit_px"], pos["exit_t"], pos["why"] = px, idx[i], "stop"
                    out.append(pos); pos = None
            if pos:
                exit_sig = False
                if rule.startswith("donchian"):
                    exit_sig = (d > 0 and row["close"] < h["lo10"].iloc[i]) or (d < 0 and row["close"] > h["hi10"].iloc[i])
                else:
                    exit_sig = h["trend"].iloc[i] == -d
                    best = max(pos["best"], row["close"]) if d > 0 else min(pos["best"], row["close"])
                    pos["best"] = best
                    ch = best - CH_MULT * h["atr"].iloc[i] if d > 0 else best + CH_MULT * h["atr"].iloc[i]
                    pos["stop"] = max(pos["stop"], ch) if d > 0 else min(pos["stop"], ch)
                if exit_sig:
                    px = nxt["open"] if d > 0 else nxt["open"] + spread
                    pos["exit_px"], pos["exit_t"], pos["why"] = px, idx[i + 1], "signal"
                    out.append(pos); pos = None
                    continue
        if pos is None:
            s = ent.iloc[i]
            if s == 0 or (longs_only and s < 0) or np.isnan(h["atr"].iloc[i]):
                continue
            fill = nxt["open"] + (spread if s > 0 else 0)
            stop_mult = 2.0 if rule.startswith("donchian") else CH_MULT
            stop = fill - s * stop_mult * h["atr"].iloc[i]
            pos = {"sym": info["symbol"], "dir": int(s), "entry_t": idx[i + 1], "fill": fill, "stop": stop,
                   "risk": abs(fill - stop), "i0": i + 1, "best": fill}
    res = []
    for p in out:
        days = max((p["exit_t"] - p["entry_t"]).days, 1)
        gross_r = p["dir"] * (p["exit_px"] - p["fill"]) / p["risk"]
        swap_r = (p["fill"] / p["risk"]) * SWAP_PCT_YEAR / 100 / 365 * days
        res.append({**{k: p[k] for k in ("sym", "dir", "entry_t", "exit_t", "why", "fill", "risk")},
                    "days": days, "r": gross_r - swap_r})
    return res


def portfolio(trades, infos):
    """Event-driven equity sim with min-lot rounding and max concurrent positions."""
    eq = BALANCE
    open_pos = []
    curve = []
    skipped = 0
    events = sorted(trades, key=lambda t: t["entry_t"])
    for t in events:
        for o in [o for o in open_pos if o["exit_t"] <= t["entry_t"]]:
            eq += o["pnl"]; open_pos.remove(o); curve.append((o["exit_t"], eq))
        if len(open_pos) >= MAX_OPEN or eq <= 1:
            skipped += 1; continue
        info = infos[t["sym"]]
        usd_per_unit_per_lot = info["tick_value"] / info["tick_size"]
        risk_per_lot = t["risk"] * usd_per_unit_per_lot
        lots = np.floor(eq * RISK_PCT / 100 / risk_per_lot / info["volume_step"]) * info["volume_step"]
        lots = max(lots, info["volume_min"])
        risk_usd = lots * risk_per_lot
        if risk_usd > eq * MAX_RISK_PCT / 100:
            skipped += 1; continue
        open_pos.append({**t, "pnl": t["r"] * risk_usd})
    for o in sorted(open_pos, key=lambda o: o["exit_t"]):
        eq += o["pnl"]; curve.append((o["exit_t"], eq))
    c = pd.Series([v for _, v in curve]) if curve else pd.Series([BALANCE])
    dd = ((c.cummax() - c) / c.cummax()).max() * 100
    return eq, dd, skipped, curve


def start_date_test(trades, infos):
    """Start the account at each Jan 1 / Jul 1: how long until 2x, and how low did it go first."""
    starts = sorted({pd.Timestamp(f"{y}-{m:02d}-01", tz="UTC") for y in range(2020, 2027) for m in (1, 7)})
    for s in starts:
        sub = [t for t in trades if t["entry_t"] >= s]
        if not sub:
            continue
        end, dd, sk, curve = portfolio(sub, infos)
        low = min([BALANCE] + [v for _, v in curve])
        hit2 = next((t for t, v in curve if v >= 2 * BALANCE), None)
        days = (hit2 - s).days if hit2 is not None else None
        print(f"    start {s.date()}: end ${end:7.2f}  lowest ${low:6.2f}  "
              f"2x after {str(days) + ' days' if days is not None else 'never'}  trades={len(sub) - sk}")


def stats(df, label):
    if df.empty:
        print(f"  {label:<26} no trades"); return
    w = df[df.r > 0]
    pf = w.r.sum() / max(-df[df.r <= 0].r.sum(), 1e-9)
    print(f"  {label:<26} n={len(df):4d} win={len(w)/len(df)*100:5.1f}% avgR={df.r.mean():+.3f} "
          f"totR={df.r.sum():+7.1f} PF={pf:4.2f}")


if __name__ == "__main__":
    d = sys.argv[1]
    data = {s: load(d, s) for s in SYMS
            if os.path.exists(os.path.join(d, f"{s}_{TF}.json")) and os.path.getsize(os.path.join(d, f"{s}_{TF}.json")) > 1000}
    infos = {s: v[1] for s, v in data.items()}
    for s, (df, _) in data.items():
        print(f"{s}: {len(df)} days from {df.index[0].date()}  spread={infos[s].get('spread')}pts")
    for rule in RULES:
        print(f"\n===== {rule}  (swap {SWAP_PCT_YEAR}%/yr) =====")
        allt = []
        for s, (df, info) in data.items():
            allt += trades_for(df, info, rule)
        t = pd.DataFrame(allt)
        if t.empty:
            print("  no trades"); continue
        stats(t, "ALL")
        for s, g in t.groupby("sym"):
            stats(g, s)
        t = t.sort_values("entry_t")
        cut = t.entry_t.iloc[int(len(t) * 0.6)]
        stats(t[t.entry_t < cut], f"first 60% (<{cut.date()})")
        stats(t[t.entry_t >= cut], "last 40%")
        for y, g in t.groupby(t.entry_t.dt.year):
            stats(g, f"year {y}")
        end_eq, dd, sk, _ = portfolio(allt, infos)
        print(f"  ${BALANCE:.0f} @ {RISK_PCT}% risk, max {MAX_OPEN} open -> ${end_eq:.2f}  maxDD={dd:.0f}%  skipped={sk}")
        if os.environ.get("START_TEST"):
            start_date_test(allt, infos)
