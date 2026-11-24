"""
Backtest for swing_strategy.py (4H crypto break-of-structure, trailing stop) using the SAME entry and
trailing code the bot runs.

  - signal on the close of 4H candle i, fill at the open of candle i+1 + spread
  - stop checked on each later candle's low (gaps through the stop fill at the open)
  - after each candle that doesn't stop out, the stop is raised exactly as swing_strategy.manage() does
  - swap: alexg_research.SWAP_PCT_DAY of the position value per day held
  - one trade per coin at a time

Also prints a Monte-Carlo of a small account (default $10): trades are drawn at random from the backtest
results to estimate the odds of doubling vs. losing half within N weeks.

Usage: python3 swing_research.py DATA_DIR [DATA_DIR ...] [--spread-mult 2] [--balance 10 --risk 5 --weeks 26]
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alexg_research as R  # noqa: E402  (loader, spreads, stats)
import swing_strategy as W  # noqa: E402


def simulate(sym, df, spread_pct):
    sig, p = W.signals(df)
    sig = sig.to_numpy()
    o, l, c, a = (p[x].to_numpy() for x in ("open", "low", "close", "atr"))
    out, pos = [], None
    for i in range(len(df) - 1):
        if pos:
            if l[i] <= pos["stop"]:
                px = min(o[i], pos["stop"])
                days = (df.index[i] - pos["t"]).total_seconds() / 86400
                r = (px - pos["fill"]) / pos["risk"] - pos["fill"] / pos["risk"] * R.SWAP_PCT_DAY * days
                out.append({"sym": sym, "dir": 1, "entry_t": pos["t"], "exit_t": df.index[i], "r": r,
                            "days": days, "risk_pct": pos["risk"] / pos["fill"] * 100, "why": "stop"})
                pos = None
            else:
                pos["best"] = max(pos["best"], c[i])
                pos["stop"] = max(pos["stop"], pos["best"] - W.STOP_ATR * a[i])
                continue
        if pos is None and sig[i]:
            fill = o[i + 1] * (1 + spread_pct)
            stop = fill - W.STOP_ATR * a[i]
            pos = {"fill": fill, "stop": stop, "risk": fill - stop, "best": fill, "t": df.index[i + 1]}
    return out


def monte_carlo(r, per_week, balance, risk_pct, weeks, runs=20000, seed=7):
    rng = np.random.default_rng(seed)
    n = rng.poisson(per_week * weeks, runs)
    end, hit_half, hit_2x, hit_5x = [], 0, 0, 0
    for k in n:
        eq, half, two, five = balance, False, False, False
        for x in rng.choice(r, size=k):
            eq *= 1 + risk_pct / 100 * x
            half |= eq <= balance / 2
            two |= eq >= balance * 2
            five |= eq >= balance * 5
        end.append(eq); hit_half += half; hit_2x += two; hit_5x += five
    end = np.array(end)
    return {"median": np.median(end), "p_half": hit_half / runs, "p_2x": hit_2x / runs, "p_5x": hit_5x / runs,
            "p_loss": (end < balance).mean()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--spread-mult", type=float, default=1.0)
    ap.add_argument("--default-spread", type=float, default=0.003, help="for coins not in CRYPTO_SPREAD_PCT")
    ap.add_argument("--balance", type=float, default=10.0)
    ap.add_argument("--risk", default="3,5,10")
    ap.add_argument("--weeks", type=int, default=26)
    a = ap.parse_args()
    data = R.load_dirs(a.dirs, "H4")
    if a.symbols:
        data = {k: v for k, v in data.items() if k in set(a.symbols.split(","))}
    allt, start, end = [], None, None
    for sym, df in data.items():
        b = R.base(sym).replace("USD", "")
        sp = next((v for k, v in R.CRYPTO_SPREAD_PCT.items() if b == k), a.default_spread) * a.spread_mult
        allt += simulate(sym, df, sp)
        start = df.index[0] if start is None else min(start, df.index[0])
        end = df.index[-1] if end is None else max(end, df.index[-1])
    t = pd.DataFrame(allt).sort_values("entry_t")
    weeks = (end - start).days / 7
    print(f"{len(data)} coins, {start.date()} -> {end.date()} ({weeks:.0f} weeks), break={W.BREAK_BARS} "
          f"stop={W.STOP_ATR}xATR, spread x{a.spread_mult}")
    R.stats(t, "ALL", weeks)
    h = len(t) // 2
    R.stats(t.iloc[:h], f"1st half (<{t.entry_t.iloc[h].date()})")
    R.stats(t.iloc[h:], "2nd half")
    for q, g in t.groupby(t.entry_t.dt.to_period("Q")):
        R.stats(g, f"quarter {q}")
    for s, g in t.groupby("sym"):
        R.stats(g, s, weeks)
    print(f"  median stop = {t.risk_pct.median():.1f}% of price, median hold = {t.days.median():.1f} days")
    per_week = len(t) / weeks
    for rp in [float(x) for x in a.risk.split(",")]:
        m = monte_carlo(t.r.to_numpy(), per_week, a.balance, rp, a.weeks)
        print(f"  ${a.balance:.0f} @ {rp}% risk, {a.weeks} weeks: median ${m['median']:.2f} | ends below start "
              f"{m['p_loss']:.0%} | falls to half {m['p_half']:.0%} | doubles {m['p_2x']:.0%} | 5x {m['p_5x']:.0%}")


if __name__ == "__main__":
    main()
