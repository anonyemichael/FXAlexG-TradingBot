"""
4H crypto swing strategy (long only) — account method "swing4h".

The higher-frequency companion to the daily trend strategy, built from Alex G's "trade the break of
structure" idea but with a trailing exit instead of his fixed 1:2.5 target (on the same breakout entries the
fixed target tested worse: profit factor 1.30 vs 1.54).

Rules (all on CLOSED 4H candles):
  Entry : the 4H candle closes above the highest high of the previous BREAK_BARS candles
          (42 candles = 7 days: a break of last week's high)      -> buy at market
  Stop  : STOP_ATR x ATR(20) below the fill
  Trail : after every 4H close the stop is raised to (highest close since entry - STOP_ATR x ATR(20));
          it is never lowered. The trade ends when the stop is hit. No fixed take-profit.

Backtest (swing_research.py, Binance 4H bars Mar 2024 - Oct 2026, spread + 15%/yr swap included):
  8 large coins (BTC ETH BNB SOL XRP DOGE ADA LTC): 3.8 trades/week, 38% winners, profit factor 1.55
  (first half 1.25, second half 1.84); 1.28 with triple spreads. LTC lost, so it is not traded.
  SOL/XRP/ADA/DOGE only (what fits a ~$10 account): 1.7 trades/week, profit factor 2.00.
  BUT on 34 other coins it only reached 1.14 (first half 0.97), so treat it as unproven and
  run it on a demo account first.
"""

import pandas as pd

import config
from alexg_strategy import closed_bars

BREAK_BARS = getattr(config, "SWING_BREAK_BARS", 42)
STOP_ATR = getattr(config, "SWING_STOP_ATR", 2.0)
ATR_LEN = 20
TRIGGER = "swing4h"
TRIGGERS = {TRIGGER}


def _prep(df):
    df = df.copy()
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_LEN).mean()
    df["brk"] = df["high"].rolling(BREAK_BARS).max().shift(1)
    return df


def signals(df_closed):
    """Boolean Series: True on each closed 4H candle that is an entry signal (backtest + live use this)."""
    df = _prep(df_closed)
    return (df["close"] > df["brk"]) & df["atr"].notna(), df


def entry_signal(symbol, df_4h, now=None):
    """df_4h: 4H bars from the bridge (a still-forming last candle is dropped by time)."""
    if df_4h is None or len(df_4h) < BREAK_BARS + ATR_LEN + 5:
        return None
    closed = closed_bars(df_4h, 4, now)
    if len(closed) < BREAK_BARS + ATR_LEN + 2:
        return None
    sig, df = signals(closed)
    if not bool(sig.iloc[-1]):
        return None
    last = df.iloc[-1]
    entry = float(last["close"])
    sl_dist = STOP_ATR * float(last["atr"])
    return {
        "symbol": symbol, "direction": "bullish",
        "entry": entry, "sl": entry - sl_dist, "sl_dist": sl_dist,
        "tp": 0.0, "tp1": None, "tp2": 0.0, "rr": 0.0,
        "grade": "SWING", "score": 100,
        "met": [f"4H close above the {BREAK_BARS}-candle high ({float(last['brk']):.6g})",
                f"Stop {STOP_ATR}x ATR({ATR_LEN}) = {sl_dist / entry * 100:.1f}%, trailed every 4H"],
        "trigger": TRIGGER, "atr": float(last["atr"]),
        "w_bias": "-", "d_trend": "-", "h4_bias": "bullish", "synced": 0, "swing_eq": 0.0, "adx": 0.0,
        "signal_time": str(closed.index[-1]),
    }


def trail_level(df_closed, opened_at):
    """Chandelier stop: highest close since entry - STOP_ATR x ATR. None if not computable."""
    df = _prep(df_closed)
    since = df[df.index >= pd.Timestamp(opened_at)]
    if since.empty or pd.isna(df["atr"].iloc[-1]):
        return None
    return float(since["close"].max()) - STOP_ATR * float(df["atr"].iloc[-1])


def manage(df_4h, opened_at, current_sl, now=None):
    """Returns ("trail", new_sl) when the stop should be raised, else (None, None)."""
    if df_4h is None or len(df_4h) < ATR_LEN + 5:
        return None, None
    opened = pd.Timestamp(opened_at)
    if opened.tzinfo is None:
        opened = opened.tz_localize("UTC")
    lvl = trail_level(closed_bars(df_4h, 4, now), opened.floor(pd.Timedelta(hours=4)))
    if lvl is not None and lvl > (current_sl or 0):
        return "trail", lvl
    return None, None
