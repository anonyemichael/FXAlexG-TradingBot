"""
Backtest for alexg_strategy.py (FXAlexG Set & Forget) on CSV bar files.

Runs the exact signal code the bot uses (alexg_strategy.compute_signals), then simulates each trade:
  - signal on the close of candle i, fill at the OPEN of candle i+1 (+ spread on buys)
  - bars are bid prices: buys exit at bid, sells pay the spread on exit
  - stop / target checked on each later candle's high/low; if both are inside one candle the STOP is assumed
  - one trade per symbol at a time (new signals are ignored while a trade is open)
  - crypto: SWAP_PCT_DAY of the position value is charged per day held
Results are in R (multiples of the risk), so they don't depend on account size. A compounding
account simulation (--balance / --risk) is printed at the end.

Usage:
  python3 alexg_research.py DATA_DIR [DATA_DIR ...] [--tf H4] [--params '{"rr": 2.5}'] [--balance 10 --risk 5]
DATA_DIR holds CSV files named SYMBOL_H4.csv / SYMBOL_H1.csv (columns time/open/high/low/close in any
of the common spellings), or JSON dumps from the bridge /ohlcv endpoint named SYMBOL_H4.json.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alexg_strategy as S  # noqa: E402

# Typical Exness Standard spreads in PRICE units (majors ~1 pip) or, for crypto, as a fraction of price.
FX_SPREAD_PIPS = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "USDJPY": 1.0, "AUDUSD": 0.9, "USDCAD": 1.4, "USDCHF": 1.2, "NZDUSD": 1.5,
    "EURJPY": 1.6, "GBPJPY": 2.2, "AUDJPY": 1.8, "CADJPY": 2.0, "CHFJPY": 2.5, "NZDJPY": 2.2,
    "EURGBP": 1.2, "EURAUD": 2.0, "EURCAD": 2.2, "EURCHF": 1.5, "EURNZD": 3.0, "GBPAUD": 2.5,
    "GBPCAD": 2.8, "GBPCHF": 2.5, "GBPNZD": 3.5, "AUDCAD": 1.8, "AUDCHF": 1.8, "AUDNZD": 2.0,
    "NZDCAD": 2.5, "NZDCHF": 2.5, "CADCHF": 2.2,
}
OTHER_SPREAD = {"XAUUSD": 0.25, "XAGUSD": 0.03, "US100": 1.5, "US500": 0.5}
CRYPTO_SPREAD_PCT = {"BTC": 0.0003, "ETH": 0.0005, "BNB": 0.001, "SOL": 0.0015, "XRP": 0.002,
                     "ADA": 0.002, "DOGE": 0.002, "LTC": 0.003}
SWAP_PCT_DAY = 0.15 / 365   # crypto overnight financing, ~15%/yr of position value


def base(sym):
    s = sym.upper().replace("USDT", "USD")
    return s[:-1] if s.endswith("M") and len(s) > 6 else s


def is_crypto(sym):
    return any(base(sym).startswith(k) for k in CRYPTO_SPREAD_PCT)


def spread_for(sym, price):
    b = base(sym)
    if is_crypto(sym):
        k = next(k for k in CRYPTO_SPREAD_PCT if b.startswith(k))
        return CRYPTO_SPREAD_PCT[k] * price * SPREAD_MULT
    if b in OTHER_SPREAD:
        return OTHER_SPREAD[b] * SPREAD_MULT
    pip = 0.01 if "JPY" in b else 0.0001
    return FX_SPREAD_PIPS.get(b, 2.5) * pip * SPREAD_MULT


SPREAD_MULT = 1.0

_COLS = {"time": ("time", "datetime", "timestamp", "t", "date", "ts", "datetime_utc", "timestamps"),
         "open": ("open", "o"), "high": ("high", "h"), "low": ("low", "l"), "close": ("close", "c")}


def load_file(path):
    if path.endswith(".json"):
        raw = json.load(open(path))
        df = pd.DataFrame(raw["bars"] if isinstance(raw, dict) else raw)
    else:
        df = pd.read_csv(path)
    lower = {c.lower(): c for c in df.columns}
    pick = {}
    for k, names in _COLS.items():
        for n in names:
            if n in lower:
                pick[k] = lower[n]; break
    t = df[pick["time"]]
    if pd.api.types.is_numeric_dtype(t):
        unit = "ms" if t.iloc[0] > 1e11 else "s"
        idx = pd.to_datetime(t, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(t, utc=True, format="mixed")
    out = pd.DataFrame({k: df[pick[k]].astype(float).to_numpy() for k in ("open", "high", "low", "close")},
                       index=pd.DatetimeIndex(idx))
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]


def load_dirs(dirs, tf):
    data = {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, f"*_{tf}.*"))):
            sym = os.path.basename(f).split(f"_{tf}")[0]
            df = load_file(f)
            if sym in data:   # same symbol from two sources: join, prefer the newer file's bars
                df = pd.concat([data[sym], df])
                df = df[~df.index.duplicated(keep="last")].sort_index()
            data[sym] = df
    return data


def simulate(sym, df, sigs, max_hold_bars=None, rr=2.5):
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    n = len(df)
    trades, busy_until = [], -1
    crypto = is_crypto(sym)
    for s in sigs:
        i = s["i"]
        if i <= busy_until or i + 1 >= n:
            continue
        d = s["direction"]
        sp = spread_for(sym, o[i + 1])
        fill = o[i + 1] + (sp if d > 0 else 0.0)
        risk = abs(fill - s["sl"])
        if (d > 0 and fill <= s["sl"]) or (d < 0 and fill >= s["sl"]) or risk <= 0:
            continue
        tp = fill + d * rr * risk    # live orders re-price the target from the actual fill, same here
        exit_px, j, why = None, i + 1, ""
        last = n - 1 if not max_hold_bars else min(n - 1, i + max_hold_bars)
        for j in range(i + 1, last + 1):
            if d > 0:
                hit_sl, hit_tp = l[j] <= s["sl"], h[j] >= tp
            else:
                hit_sl, hit_tp = h[j] + sp >= s["sl"], l[j] + sp <= tp
            if hit_sl:
                gap = (o[j] < s["sl"]) if d > 0 else (o[j] + sp > s["sl"])
                exit_px = (o[j] if d > 0 else o[j] + sp) if gap and j > i + 1 else s["sl"]
                why = "sl"; break
            if hit_tp:
                exit_px, why = tp, "tp"; break
        if exit_px is None:
            exit_px, why = (c[last] if d > 0 else c[last] + sp), "open" if last == n - 1 else "time"
            j = last
        r = d * (exit_px - fill) / risk
        days = max((df.index[j] - df.index[i + 1]).total_seconds() / 86400, 0)
        if crypto:
            r -= fill / risk * SWAP_PCT_DAY * days
        trades.append({"sym": sym, "dir": d, "entry_t": df.index[i + 1], "exit_t": df.index[j], "why": why,
                       "r": r, "days": days, "risk_pct": risk / fill * 100, "sync": s["sync"]})
        busy_until = j
    return trades


def stats(t, label, weeks=None):
    if t.empty:
        print(f"  {label:<28} no trades"); return
    w = t[t.r > 0]
    pf = w.r.sum() / max(-t[t.r <= 0].r.sum(), 1e-9)
    eq = t.sort_values("exit_t").r.cumsum()
    dd = (eq.cummax().clip(lower=0) - eq).max()
    per_wk = f" {len(t) / weeks:5.2f}/wk" if weeks else ""
    print(f"  {label:<28} n={len(t):4d}{per_wk} win={len(w) / len(t) * 100:5.1f}% avgR={t.r.mean():+.3f} "
          f"totR={t.r.sum():+7.1f} PF={pf:4.2f} maxDD={dd:5.1f}R")


def account_sim(t, balance, risk_pct, max_open):
    """Compounding: each trade risks risk_pct of the balance at entry; pnl booked at exit."""
    eq, open_pos, low, peak, maxdd, taken = balance, [], balance, balance, 0.0, 0
    for _, tr in t.sort_values("entry_t").iterrows():
        for p in sorted([p for p in open_pos if p["exit_t"] <= tr.entry_t], key=lambda p: p["exit_t"]):
            eq += p["pnl"]; open_pos.remove(p)
            peak = max(peak, eq); low = min(low, eq); maxdd = max(maxdd, (peak - eq) / peak)
        if len(open_pos) >= max_open or eq <= 0.5:
            continue
        open_pos.append({"exit_t": tr.exit_t, "pnl": tr.r * eq * risk_pct / 100}); taken += 1
    for p in sorted(open_pos, key=lambda p: p["exit_t"]):
        eq += p["pnl"]; peak = max(peak, eq); low = min(low, eq); maxdd = max(maxdd, (peak - eq) / peak)
    return eq, low, maxdd * 100, taken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--tf", default="H4")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--spread-mult", type=float, default=1.0)
    ap.add_argument("--max-hold", type=int, default=0)
    ap.add_argument("--balance", type=float, default=10.0)
    ap.add_argument("--risk", type=float, default=0)
    ap.add_argument("--max-open", type=int, default=3)
    ap.add_argument("--per-symbol", action="store_true")
    ap.add_argument("--since", default="")
    a = ap.parse_args()
    global SPREAD_MULT
    SPREAD_MULT = a.spread_mult
    params = json.loads(a.params)
    data = load_dirs(a.dirs, a.tf)
    if a.symbols:
        keep = set(a.symbols.split(","))
        data = {k: v for k, v in data.items() if k in keep}
    allt = []
    span_start, span_end = None, None
    for sym, df in data.items():
        sigs = S.compute_signals(df, params)
        if a.since:
            sigs = [s for s in sigs if s["time"] >= pd.Timestamp(a.since, tz="UTC")]
        allt += simulate(sym, df, sigs, a.max_hold or None, {**S.DEFAULTS, **params}["rr"])
        first = df.index[0] + pd.Timedelta(days=60) if not a.since else pd.Timestamp(a.since, tz="UTC")
        span_start = first if span_start is None else min(span_start, first)
        span_end = df.index[-1] if span_end is None else max(span_end, df.index[-1])
    t = pd.DataFrame(allt)
    weeks = (span_end - span_start).days / 7 if span_start is not None else None
    print(f"{len(data)} symbols, {a.tf}, {span_start.date() if span_start is not None else '-'} -> "
          f"{span_end.date() if span_end is not None else '-'} ({weeks:.0f} weeks), params={params}, spread x{SPREAD_MULT}")
    if t.empty:
        print("no trades"); return
    stats(t, "ALL", weeks)
    stats(t[t.dir > 0], "longs", weeks)
    stats(t[t.dir < 0], "shorts", weeks)
    t = t.sort_values("entry_t")
    cut = t.entry_t.iloc[len(t) // 2]
    stats(t[t.entry_t < cut], f"1st half (<{cut.date()})")
    stats(t[t.entry_t >= cut], "2nd half")
    for y, g in t.groupby(t.entry_t.dt.year):
        stats(g, f"year {y}")
    if a.per_symbol:
        for s, g in t.groupby("sym"):
            stats(g, s)
    print(f"  median stop = {t.risk_pct.median():.2f}% of price, median hold = {t.days.median():.1f} days")
    if a.risk:
        end, low, dd, n = account_sim(t, a.balance, a.risk, a.max_open)
        print(f"  ${a.balance:.0f} @ {a.risk}% risk/trade, max {a.max_open} open: -> ${end:,.2f}  "
              f"lowest ${low:.2f}  maxDD {dd:.0f}%  trades taken {n}")


if __name__ == "__main__":
    main()
