"""
Day trading vs. day+swing research (results: DAY_SWING_RESEARCH.md).

Day trading = every trade closed by the end of the day. Five families, both directions, on gold + 11 forex
pairs (1H, 2012-2019 design / 2020-2025 later years, data as in gold_research.py) and 10 coins (1H Binance
klines, 2022-2023 design / 2024-2026 later years, github.com/finom/static-klines .klines-cache/<COIN>/1h).
  pdb   prior-day high/low breakout in the daily trend      orb   opening-range breakout
  imom  intraday momentum (morning move -> afternoon)        pull  RSI(2) pullback in the daily trend
  chan  12/24-candle channel breakout, flat at day's end
Then the hybrid: the live crypto 1H engine (momentum_strategy "mom_1h": 168-candle breakout, 3 ATR trail, BTC
EMA100 filter) and the holding-time split of the gold 1H rule.

Usage:  FX_DATA=... CRYPTO_1H=... python3 day_research.py day      # the day-trading families
        FX_DATA=... CRYPTO_1H=... python3 day_research.py hybrid   # crypto 1H engine + holding times
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gold_research as L  # noqa: E402  (loaders, simulator, stats, families)
import momentum_strategy as M  # noqa: E402

R = L
SK = os.environ.get("CRYPTO_1H", "data/static-klines/.klines-cache")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"]
CSPLIT = pd.Timestamp("2024-01-01")
_c = {}

def crypto(sym):
    if sym in _c: return _c[sym]
    rows = []
    for f in sorted(glob.glob(f"{SK}/{sym}/1h/*.json")):
        rows += json.load(open(f))
    df = pd.DataFrame([r[:5] for r in rows], columns=["t", "open", "high", "low", "close"])
    df.index = pd.to_datetime(df["t"], unit="ms")
    df = df[["open", "high", "low", "close"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    _c[sym] = df
    return df

def cfrac(sym):          # Exness-like round-trip spread as a fraction of price
    return 0.0006 if sym in ("BTCUSDT", "ETHUSDT") else 0.0012

def daytrend(df, filt):
    if not filt: return np.zeros(len(df))
    d1 = L.to_d1(df); e = L.ema(d1["close"], filt)
    return np.sign(L.d1_on(df, d1["close"] - e))

def prev_day(df):
    day = df.index.normalize()
    g = df.groupby(day).agg(h=("high", "max"), l=("low", "min"), o=("open", "first"), c=("close", "last"))
    p = g.shift(1)
    return p["h"].reindex(day).values, p["l"].reindex(day).values, g["o"].reindex(day).values

# ── families: each returns simulate kwargs; hours are the data's clock (gold/FX server GMT+2/3, crypto UTC) ──
def pdb(df, cls, k, tp, filt, w0, w1, eod):
    """Prior-day high/low breakout in the daily trend direction."""
    ph, pl, _ = prev_day(df); tr = daytrend(df, filt); a = L.atr(df, 20).values
    c = df["close"].values; hr = df.index.hour
    win = (hr >= w0) & (hr < w1)
    up = win & (c > ph) & (tr >= 0); dn = win & (c < pl) & (tr <= 0)
    return dict(long_sig=up, short_sig=dn, stop_dist=k * a, tp_r=tp, eod_hours=eod, one_per_day=True)

def orb(df, cls, nbars, k, tp, filt, w0, w1, eod):
    """Opening range = first `nbars` hourly candles from hour w0; first close outside it before w1."""
    hr = df.index.hour; day = df.index.normalize()
    inr = (hr >= w0) & (hr < w0 + nbars)
    g = pd.DataFrame({"h": np.where(inr, df["high"], np.nan), "l": np.where(inr, df["low"], np.nan), "d": day})
    rh = g.groupby("d")["h"].transform("max").values; rl = g.groupby("d")["l"].transform("min").values
    tr = daytrend(df, filt); c = df["close"].values; a = L.atr(df, 20).values
    win = (hr >= w0 + nbars) & (hr < w1)
    up = win & (c > rh) & (tr >= 0); dn = win & (c < rl) & (tr <= 0)
    sd = np.where(up, c - rl, np.where(dn, rh - c, np.nan)) if k == 0 else k * a
    return dict(long_sig=up, short_sig=dn, stop_dist=sd, tp_r=tp, eod_hours=eod, one_per_day=True)

def imom(df, cls, h_sig, k, filt, eod):
    """Intraday momentum: at hour h_sig, trade the sign of the move since the day's open until the close."""
    _, _, do = prev_day(df); tr = daytrend(df, filt); c = df["close"].values; a = L.atr(df, 20).values
    at = df.index.hour == h_sig
    up = at & (c > do) & (tr >= 0); dn = at & (c < do) & (tr <= 0)
    return dict(long_sig=up, short_sig=dn, stop_dist=k * a, eod_hours=eod, one_per_day=True)

def pull(df, cls, lo, k, filt, w0, w1, eod):
    """Pullback in the daily trend: H1 RSI(2) extreme against the trend, exit on close back over SMA5 or EOD."""
    r = L.rsi(df["close"], 2).values; tr = daytrend(df, filt); c = df["close"].values; a = L.atr(df, 20).values
    sma = df["close"].rolling(5).mean().values; hr = df.index.hour; win = (hr >= w0) & (hr < w1)
    return dict(long_sig=win & (r < lo) & (tr > 0), short_sig=win & (r > 100 - lo) & (tr < 0), stop_dist=k * a,
                exit_long=c > sma, exit_short=c < sma, eod_hours=eod)

def chan(df, cls, N, k, tp, filt, w0, w1, eod):
    """H1 channel breakout of the last N candles in the trend direction, flat at EOD."""
    hh = df["high"].rolling(N).max().shift(1).values; ll = df["low"].rolling(N).min().shift(1).values
    tr = daytrend(df, filt); c = df["close"].values; a = L.atr(df, 20).values; hr = df.index.hour
    win = (hr >= w0) & (hr < w1)
    return dict(long_sig=win & (c > hh) & (tr >= 0), short_sig=win & (c < ll) & (tr <= 0), stop_dist=k * a,
                trail=k * a, tp_r=tp, eod_hours=eod, one_per_day=True)

def run(label, fn, params_by_cls, classes=("gold", "fx", "crypto")):
    line = f"{label:44s}"
    for cls in classes:
        if cls not in params_by_cls: continue
        p = params_by_cls[cls]
        for seg in ("des", "oos"):
            trades, weeks = [], 1
            if cls == "crypto":
                for s in COINS:
                    df = crypto(s); st, en = (None, CSPLIT) if seg == "des" else (CSPLIT, None)
                    old = L.spread; L.spread = lambda x: 0.0
                    trades += L.simulate(df, s, start=st, end=en, cost_frac=cfrac(s), **fn(df, cls, *p))
                    L.spread = old
                weeks = (104 if seg == "des" else 99)
            else:
                syms = ["XAUUSD"] if cls == "gold" else [x for x in L.DESIGN if x != "XAUUSD"]
                for s in syms:
                    df = L.load(s, "H1"); st, en = (None, L.SPLIT) if seg == "des" else (L.SPLIT, None)
                    trades += L.simulate(df, s, start=st, end=en, **fn(df, cls, *p))
                weeks = 370 if seg == "des" else 313
            x = L.stats(trades)
            if x["n"]:
                line += f" | {cls[:2]}{seg[0]} n={x['n']:5d} {x['n']/weeks:5.1f}/wk PF {x['pf']:4.2f} (L{x['long_pf']} S{x['short_pf']})"
        line += " ‖"
    print(line, flush=True)

GOLD_EOD, CR_EOD = {22, 23, 0, 1}, {23}
FAM = sys.argv[1] if len(sys.argv) > 1 else "day"
DAY = FAM in ("day", "all")
if FAM in ("pdb",) or DAY:
    for k in (1.0, 1.5, 2.0):
        for tp in (1.0, 2.0, None):
            for f in (0, 100):
                run(f"pdb k{k} tp{tp} f{f}", pdb, {"gold": (k, tp, f, 9, 20, GOLD_EOD), "fx": (k, tp, f, 9, 20, GOLD_EOD),
                                                  "crypto": (k, tp, f, 1, 21, CR_EOD)})
if FAM in ("orb",) or DAY:
    for nb in (1, 2):
        for k in (0, 1.5):
            for tp in (1.0, 2.0, None):
                for f in (0, 100):
                    run(f"orb {nb}h stop{'range' if k == 0 else k} tp{tp} f{f}", orb,
                        {"gold": (nb, k, tp, f, 10, 20, GOLD_EOD), "fx": (nb, k, tp, f, 10, 20, GOLD_EOD),
                         "crypto": (nb, k, tp, f, 0, 21, CR_EOD)})
if FAM in ("imom",) or DAY:
    for hs in (14, 16, 18):
        for k in (1.5, 3.0):
            for f in (0, 100):
                run(f"imom @{hs}h k{k} f{f}", imom, {"gold": (hs, k, f, GOLD_EOD), "fx": (hs, k, f, GOLD_EOD),
                                                   "crypto": (hs - 2, k, f, CR_EOD)})
if FAM in ("pull",) or DAY:
    for lo in (5, 10):
        for k in (1.5, 3.0):
            for f in (50, 100):
                run(f"pull rsi2<{lo} k{k} f{f}", pull, {"gold": (lo, k, f, 9, 20, GOLD_EOD), "fx": (lo, k, f, 9, 20, GOLD_EOD),
                                                      "crypto": (lo, k, f, 0, 21, CR_EOD)})
if FAM in ("chan",) or DAY:
    for N in (12, 24):
        for k in (1.5, 2.0):
            for tp in (2.0, None):
                for f in (0, 100):
                    run(f"chan N{N} k{k} tp{tp} f{f}", chan, {"gold": (N, k, tp, f, 9, 20, GOLD_EOD), "fx": (N, k, tp, f, 9, 20, GOLD_EOD),
                                                          "crypto": (N, k, tp, f, 0, 21, CR_EOD)})


def crypto_1h_engine(N=None, k=None):
    """The live mom_1h rule with the live functions (SMA ATR, BTC EMA100 filter from the previous day)."""
    e = M.ENGINES["mom_1h"]
    N, k = N or e["break"], k or e["atr_mult"]
    bd = L.to_d1(crypto("BTCUSDT"))
    ok_by_day = M.regime_series(bd).shift(1)
    out = {}
    for seg, st, en in (("2022-23", None, CSPLIT), ("2024-26", CSPLIT, None)):
        tr = []
        for s in COINS:
            df = crypto(s)
            hh = df["high"].rolling(N).max().shift(1).values
            ok = ok_by_day.reindex(df.index.floor("1D")).fillna(0).to_numpy() > 0
            a = M.atr(df).values
            old = L.spread
            L.spread = lambda x: 0.0
            tr += L.simulate(df, s, (df["close"].values > hh) & ok, np.zeros(len(df), bool), k * a, trail=k * a,
                             start=st or df.index[24 * 210], end=en, cost_frac=cfrac(s))
            L.spread = old
        out[seg] = tr
    return out


def holding(trades, label):
    d = np.array([t["days"] for t in trades]); r = np.array([t["R"] for t in trades])
    for lo, hi, lab in ((0, 1, "closed within 24h"), (1, 3, "1-3 days"), (3, 99, "over 3 days")):
        m = (d >= lo) & (d < hi)
        print(f"{label}, {lab:18s}: {m.mean() * 100:4.0f}% of trades, total {r[m].sum():+7.1f}R")


if FAM in ("hybrid", "all"):
    res = crypto_1h_engine()
    for seg, tr in res.items():
        x = L.stats(tr)
        print(f"crypto 1H engine {seg}: n={x['n']} PF {x['pf']} win {x['win']}%")
    holding(res["2024-26"], "crypto 1H engine 2024-26")
    import metals_strategy as MS
    g = L.load("XAUUSD", "H1")
    d1 = L.to_d1(g)
    tr = np.sign(L.d1_on(g, d1["close"] - L.ema(d1["close"], MS.TREND_EMA)))
    ls, ss, sd = MS.signal_arrays(g, tr)
    holding(L.simulate(g, "XAUUSD", ls, ss, sd, trail=sd), "gold 1H rule 2012-2025")
