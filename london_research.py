"""
London-open breakout research (H1 bars). Standard rules, fixed in advance:
  - Asian box = high/low of the 00:00–06:59 UTC H1 bars.
  - 07:00–10:59 UTC: the first bar trading through the box high goes long at the box high
    (or at that bar's open if it gapped through); through the box low goes short. One trade/day/symbol.
  - Stop = opposite side of the box. Target = TP_R x box height (tested at 1R and 2R).
  - Anything still open at 20:00 UTC is closed at that bar's open.
  - Costs: live spread from bridge symbol info. If stop and target fall in the same bar, stop is assumed.
Usage: SYMS=EURUSDm,... TP_R=1 python3 london_research.py DATA_DIR
"""

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trend_research as tr  # reuse portfolio() / stats()

TP_R = float(os.environ.get("TP_R", 1.0))
SYMS = os.environ.get("SYMS", "EURUSDm,GBPUSDm,USDJPYm,AUDUSDm,USDCADm,EURJPYm,GBPJPYm,XAUUSDm").split(",")


def load_h1(d, s):
    raw = json.load(open(os.path.join(d, f"{s}_H1.json")))["bars"]
    df = pd.DataFrame(raw)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.set_index("time")[["open", "high", "low", "close"]].astype(float)


def trades_for(df, info):
    spread = info.get("spread", 0) * info["tick_size"]
    out = []
    for day, g in df.groupby(df.index.floor("1D")):
        if day.weekday() >= 5:
            continue
        asia = g[g.index.hour < 7]
        if len(asia) < 6:
            continue
        hi, lo = asia["high"].max(), asia["low"].min()
        box = hi - lo
        if box <= 0:
            continue
        sess = g[(g.index.hour >= 7) & (g.index.hour < 20)]
        pos = None
        for t, b in sess.iterrows():
            if pos is None:
                if t.hour >= 11:
                    break
                up = b["high"] + spread >= hi
                dn = b["low"] <= lo
                if up and dn:
                    break  # both sides in one bar: ambiguous, skip the day
                if up:
                    fill = max(b["open"] + spread, hi)
                    pos = {"dir": 1, "fill": fill, "stop": lo, "tp": fill + TP_R * (fill - lo), "entry_t": t}
                elif dn:
                    fill = min(b["open"], lo)
                    pos = {"dir": -1, "fill": fill, "stop": hi, "tp": fill - TP_R * (hi - fill), "entry_t": t}
                if pos is None:
                    continue
                pos["risk"] = abs(pos["fill"] - pos["stop"])
                # the entry bar itself can also hit stop / target after the fill — check it below
            d = pos["dir"]
            bh, bl = (b["high"], b["low"]) if d > 0 else (b["high"] + spread, b["low"] + spread)
            hit_sl = bl <= pos["stop"] if d > 0 else bh >= pos["stop"]
            hit_tp = bh >= pos["tp"] if d > 0 else bl <= pos["tp"]
            exit_px = None
            if hit_sl:
                exit_px = pos["stop"]
            elif hit_tp:
                exit_px = pos["tp"]
            if exit_px is not None:
                out.append({"sym": info["symbol"], "dir": d, "entry_t": pos["entry_t"], "exit_t": t,
                            "risk": pos["risk"], "fill": pos["fill"], "why": "sl" if hit_sl else "tp",
                            "r": d * (exit_px - pos["fill"]) / pos["risk"]})
                pos = None
                break
        if pos is not None:  # time exit at 20:00
            last = sess.iloc[-1]
            px = last["close"] if pos["dir"] > 0 else last["close"] + spread
            out.append({"sym": info["symbol"], "dir": pos["dir"], "entry_t": pos["entry_t"], "exit_t": sess.index[-1],
                        "risk": pos["risk"], "fill": pos["fill"], "why": "time",
                        "r": pos["dir"] * (px - pos["fill"]) / pos["risk"]})
    return out


if __name__ == "__main__":
    d = sys.argv[1]
    allt, infos = [], {}
    for s in SYMS:
        p = os.path.join(d, f"{s}_H1.json")
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            continue
        info = json.load(open(os.path.join(d, f"{s}_info.json")))
        infos[s] = info
        df = load_h1(d, s)
        print(f"{s}: {len(df)} H1 bars from {df.index[0].date()}")
        allt += trades_for(df, info)
    t = pd.DataFrame(allt)
    print(f"\n===== London breakout TP={TP_R}R =====")
    tr.stats(t, "ALL")
    for s, g in t.groupby("sym"):
        tr.stats(g, s)
    t = t.sort_values("entry_t")
    cut = t.entry_t.iloc[int(len(t) * 0.6)]
    tr.stats(t[t.entry_t < cut], f"first 60% (<{cut.date()})")
    tr.stats(t[t.entry_t >= cut], "last 40%")
    print(t.why.value_counts().to_string())
    end, dd, sk, _ = tr.portfolio(allt, infos)
    print(f"  ${tr.BALANCE:.0f} @ {tr.RISK_PCT}% risk, max {tr.MAX_OPEN} open -> ${end:.2f}  maxDD={dd:.0f}%  skipped={sk}")
