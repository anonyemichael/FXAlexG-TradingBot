"""
Gold 1H Breakout — trades BOTH directions (buy and sell). Research: GOLD_FOREX_RESEARCH.md.

Of every gold/forex family tested (trend breakouts, moving-average crosses, RSI(2) and Bollinger mean
reversion, London-open breakouts; 11 pairs + gold 2012-2025, 16 unseen pairs + silver 2024-2026), only metal
trend breakouts made money after costs in both the design years (2012-2019) and the later years (2020-2025).
Forex pairs did not.

  Direction filter  gold's last daily close vs its 200-day EMA: above -> only buys, below -> only sells.
  Entry             a 1H candle closes above the highest high of the previous 120 1H candles (the last
                    ~5 trading days)  -> BUY;  below the lowest low of those candles -> SELL.
                    If the run comes one candle late, the trade is still taken while price is beyond the level.
  Stop              2 x ATR(20) on 1H from the entry (~0.5% of price), trailed after every 1H close:
                    buys: highest close since entry - 2 ATR; sells: lowest close since entry + 2 ATR.
                    Never loosened. No fixed take-profit.
Backtest (costs, next-candle fills): 2012-19 PF 1.44 (buys 1.43, sells 1.45); 2020-25 PF 1.31 (buys 1.30,
sells 1.40); ~1 trade a week. gold_research.py uses the functions below, so the backtest is the live code.
"""

import numpy as np
import pandas as pd

import config

SYMBOLS = getattr(config, "METALS_SYMBOLS", ["XAUUSDm"])
BREAK = getattr(config, "METALS_BREAKOUT", 120)
STOP_ATR = getattr(config, "METALS_STOP_ATR", 2.0)
TREND_EMA = getattr(config, "METALS_TREND_EMA", 200)
ATR_LEN = 20
TF_HOURS = 1
LABEL = "Gold 1H"
COMMENT = "MET 1H"


def closed_bars(df, tf_hours, now=None):
    if df is None or df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    return df[(idx + pd.Timedelta(hours=tf_hours)) <= now]


def atr(df, n=ATR_LEN):
    """Wilder ATR (as in the research)."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def trend(d1_closed):
    """(+1 / -1 / 0, last close, EMA) from closed daily candles."""
    if d1_closed is None or len(d1_closed) < TREND_EMA // 2:
        return 0, None, None
    c = d1_closed["close"]
    e = c.ewm(span=TREND_EMA, adjust=False).mean()
    return int(np.sign(c.iloc[-1] - e.iloc[-1])), float(c.iloc[-1]), float(e.iloc[-1])


def channel(h1_closed):
    """Arrays: breakout high/low of the previous BREAK candles, ATR."""
    hh = h1_closed["high"].rolling(BREAK).max().shift(1)
    ll = h1_closed["low"].rolling(BREAK).min().shift(1)
    return hh.to_numpy(), ll.to_numpy(), atr(h1_closed).to_numpy()


def entry_signal(symbol, h1, d1, now=None):
    """Live: setup dict for a breakout on the last (or, if the run was late, the previous) closed 1H candle,
    in the direction allowed by the daily trend. None if nothing to do."""
    if h1 is None or d1 is None:
        return None
    bars = closed_bars(h1, TF_HOURS, now)
    if bars is None or len(bars) < BREAK + ATR_LEN + 3:
        return None
    tr, dclose, ema = trend(closed_bars(d1, 24, now))
    if tr == 0:
        return None
    hh, ll, a = channel(bars)
    c = bars["close"].to_numpy()
    for j in (-1, -2):                       # breakout on the last candle, or one candle late
        lvl = hh[j] if tr > 0 else ll[j]
        if not np.isfinite(lvl) or not np.isfinite(a[j]):
            continue
        broke = c[j] > lvl if tr > 0 else c[j] < lvl
        still = c[-1] > lvl if tr > 0 else c[-1] < lvl
        if broke and still:
            sl_dist = STOP_ATR * float(a[-1])
            side = "above" if tr > 0 else "below"
            return {
                "symbol": symbol, "dir": tr, "sl_dist": sl_dist, "level": float(lvl), "atr": float(a[-1]),
                "signal_time": str(bars.index[j]), "price": float(c[-1]),
                "met": [f"gold {side} its {TREND_EMA}-day average ({dclose:,.2f} vs {ema:,.2f}) → "
                        f"{'buys' if tr > 0 else 'sells'} only",
                        f"1H close {side} the last {BREAK} candles' {'high' if tr > 0 else 'low'} ({lvl:,.2f})"],
            }
    return None


def manage(h1, direction, opened_at, current_sl, entry_price, now=None):
    """New trailing stop (only if it tightens), else None."""
    bars = closed_bars(h1, TF_HOURS, now)
    if bars is None or len(bars) < ATR_LEN + 2:
        return None
    opened = pd.Timestamp(opened_at)
    if opened.tzinfo is None:
        opened = opened.tz_localize("UTC")
    since = bars[bars.index >= opened.floor("1h")]
    a = float(atr(bars).iloc[-1])
    if since.empty or not np.isfinite(a):
        return None
    if direction > 0:
        best = float(since["close"].max()) if not entry_price else max(float(since["close"].max()), entry_price)
        lvl = best - STOP_ATR * a
        return lvl if lvl > (current_sl or 0) else None
    best = float(since["close"].min()) if not entry_price else min(float(since["close"].min()), entry_price)
    lvl = best + STOP_ATR * a
    return lvl if (not current_sl or lvl < current_sl) else None


def signal_arrays(h1, d1_trend_by_bar):
    """Backtest helper: long/short flags on each closed candle (trend = previous day's value per bar)."""
    hh, ll, a = channel(h1)
    c = h1["close"].to_numpy()
    tr = np.asarray(d1_trend_by_bar)
    return (c > hh) & (tr > 0), (c < ll) & (tr < 0), STOP_ATR * a
