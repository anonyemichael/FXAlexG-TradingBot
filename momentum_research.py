"""
Backtest for momentum_strategy.py using the same functions the bot runs.

Usage:
  python3 momentum_research.py --h4 DIR [DIR..] --d1 DIR [DIR..] [--core BTCUSD,ETHUSD,...]
                               [--spread-mult 2] [--balance 10 --risk 5 --max-open 3]
DIRs hold SYMBOL_H4.csv / SYMBOL_D1.csv (or bridge JSON dumps). BTCUSD daily must be among the --d1 data
(market filter). "core" coins = the ones the rules were designed on; every other coin is out-of-sample.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alexg_research as R  # noqa: E402  (CSV/JSON loader, spread table, stats)
import momentum_strategy as M  # noqa: E402

CORE = "BTCUSD,ETHUSD,BNBUSD,SOLUSD,XRPUSD,DOGEUSD,ADAUSD,LTCUSD"
STABLE = {"BFUSDUSD", "FRAXUSD", "RLUSDUSD", "PAXGUSD", "XAUTUSD", "USD1USD", "FDUSDUSD", "USDEUSD"}


def spread_of(sym, mult, default):
    return R.CRYPTO_SPREAD_PCT.get(R.base(sym).replace("USD", ""), default) * mult


def run_engine(data, engine, btc, mult, default_spread):
    out = []
    for sym, df in data.items():
        flags = M.regime_on_index(df.index, btc, engine)
        out += M.backtest_symbol(sym, df, engine, flags, spread_of(sym, mult, default_spread))
    return pd.DataFrame(out)


def pf(t):
    return t[t.r > 0].r.sum() / max(-t[t.r <= 0].r.sum(), 1e-9) if len(t) else float("nan")


def line(t, label, weeks=None):
    if t.empty:
        return f"  {label:<34} no trades"
    t = t.sort_values("entry_t")
    h = len(t) // 2
    yrs = t.groupby(t.entry_t.dt.year).r.sum()
    wk = f"{len(t) / weeks:5.2f}/wk " if weeks else ""
    return (f"  {label:<34} n={len(t):5d} {wk}win={100 * (t.r > 0).mean():4.1f}% avgR={t.r.mean():+.2f} "
            f"PF={pf(t):4.2f} [halves {pf(t.iloc[:h]):4.2f} / {pf(t.iloc[h:]):4.2f}] years+ {int((yrs > 0).sum())}/{len(yrs)} "
            f"stop~{t.stop_pct.median():.1f}%")


def portfolio(t, balance, risk_pct, max_open, start=None, weeks=None):
    """Compounding account: risk_pct of the balance per trade, at most max_open trades, one per coin."""
    t = t.sort_values("entry_t")
    if start is not None:
        t = t[t.entry_t >= start]
        if weeks:
            t = t[t.entry_t < start + pd.Timedelta(weeks=weeks)]
    eq, low, peak, mdd, open_, taken = balance, balance, balance, 0.0, [], 0
    for _, tr in t.iterrows():
        for p in sorted([p for p in open_ if p["exit_t"] <= tr.entry_t], key=lambda p: p["exit_t"]):
            eq += p["pnl"]; open_.remove(p)
            peak = max(peak, eq); low = min(low, eq); mdd = max(mdd, 1 - eq / peak)
        if len(open_) >= max_open or any(p["sym"] == tr.sym for p in open_) or eq <= 0.5:
            continue
        open_.append({"exit_t": tr.exit_t, "sym": tr.sym, "pnl": tr.r * eq * risk_pct / 100}); taken += 1
    for p in sorted(open_, key=lambda p: p["exit_t"]):
        eq += p["pnl"]; peak = max(peak, eq); low = min(low, eq); mdd = max(mdd, 1 - eq / peak)
    return eq, low, mdd * 100, taken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h4", nargs="*", default=[])
    ap.add_argument("--d1", nargs="+", required=True)
    ap.add_argument("--core", default=CORE)
    ap.add_argument("--spread-mult", type=float, default=1.0)
    ap.add_argument("--default-spread", type=float, default=0.003)
    ap.add_argument("--balance", type=float, default=10.0)
    ap.add_argument("--risk", type=float, default=5.0)
    ap.add_argument("--max-open", type=int, default=3)
    a = ap.parse_args()
    core = set(a.core.split(","))

    d1 = {k: v for k, v in R.load_dirs(a.d1, "D1").items() if k not in STABLE and len(v) > 150}
    btc = d1["BTCUSD"]
    h4 = {k: v for k, v in R.load_dirs(a.h4, "H4").items() if k not in STABLE} if a.h4 else {}

    print(f"Daily data: {len(d1)} coins, {min(v.index[0] for v in d1.values()).date()} -> "
          f"{max(v.index[-1] for v in d1.values()).date()};  4H data: {len(h4)} coins")
    print(f"Market filter: BTC close > EMA{M.REGIME_EMA}. Spread x{a.spread_mult}.")

    td = run_engine(d1, "mom_d1", btc, a.spread_mult, a.default_spread)
    print("\nDAILY ENGINE (20-day high, 3xATR trail)")
    print(line(td[td.sym.isin(core) & (td.entry_t < pd.Timestamp("2023-01-01", tz="UTC"))], "core coins 2018-2022 (design)"))
    print(line(td[td.sym.isin(core) & (td.entry_t >= pd.Timestamp("2023-01-01", tz="UTC"))], "core coins 2023-2026"))
    print(line(td[~td.sym.isin(core)], f"{td[~td.sym.isin(core)].sym.nunique()} other coins (out-of-sample)"))
    for y, g in td.groupby(td.entry_t.dt.year):
        print(line(g, f"  all coins {y}"))

    if h4:
        t4 = run_engine(h4, "mom_4h", btc, a.spread_mult, a.default_spread)
        span = (max(v.index[-1] for v in h4.values()) - min(v.index[0] for v in h4.values())).days / 7
        print("\n4H ENGINE (42-candle high, 2.5xATR trail)")
        print(line(t4[t4.sym.isin(core)], "core coins", span))
        print(line(t4[~t4.sym.isin(core)], f"{t4[~t4.sym.isin(core)].sym.nunique()} other coins (out-of-sample)", span))
        for q, g in t4.groupby(t4.entry_t.dt.to_period("Q")):
            print(line(g, f"  all coins {q}"))

        # combined account over the 4H data window, coins that have both timeframes
        both = sorted(set(h4) & set(d1))
        start = max(min(h4[s].index[0] for s in both) + pd.Timedelta(days=10), pd.Timestamp("2024-04-01", tz="UTC"))
        comb = pd.concat([t4[t4.sym.isin(both)], td[td.sym.isin(both)]])
        comb = comb[comb.entry_t >= start]
        weeks = (max(h4[s].index[-1] for s in both) - start).days / 7
        print(f"\nCOMBINED (both engines, {len(both)} coins, {start.date()} ->, {weeks:.0f} weeks)")
        print(line(comb, "all trades", weeks))
        for grp, name in ((core, "core coins only"),):
            g = comb[comb.sym.isin(grp)]
            print(line(g, name, weeks))
        print(f"\n${a.balance:.0f} account, {a.risk}% risk per trade, max {a.max_open} open, one per coin:")
        for label, tt in (("core coins, 4H engine only", t4[t4.sym.isin(core)]),
                          ("core coins, both engines", comb[comb.sym.isin(core)]),
                          ("all coins, both engines", comb)):
            for st in pd.date_range(start, periods=5, freq="26W"):
                end, low, dd, n = portfolio(tt, a.balance, a.risk, a.max_open, st, 26)
                if n:
                    print(f"  {label:<28} start {st.date()}: 26 weeks -> ${end:7.2f}  lowest ${low:6.2f}  "
                          f"maxDD {dd:4.0f}%  trades {n}")

    print(f"\nLong run, daily engine only, core coins, {a.risk}% risk, max {a.max_open} open (needs ~$100+ to size):")
    core_d = td[td.sym.isin(core)]
    for st in pd.date_range("2018-06-01", "2026-01-01", freq="YS", tz="UTC"):
        end, low, dd, n = portfolio(core_d, 100.0, a.risk, a.max_open, st, 52)
        print(f"  $100 from {st.date()}: 1 year -> ${end:8.2f}  lowest ${low:7.2f}  maxDD {dd:4.0f}%  trades {n}")


if __name__ == "__main__":
    main()
