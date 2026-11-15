"""
Daily trend-following strategy (long-only) — accounts assigned "trend" or "trend_fast".

Rules (identical to trend_research.py rule "ema_l", which was profitable in both the in-sample and
out-of-sample halves of 2019–2026 data, and across every neighbouring EMA / stop setting tested):
  Entry : on the daily close, EMA(fast) crosses above EMA(slow)  -> buy at market
  Stop  : 3 x ATR(20) below the fill, then trailed to (highest daily close since entry - 3 x ATR)
  Exit  : EMA(fast) closes back below EMA(slow) -> close at market (or the trailing stop is hit)
  No fixed take-profit: trend trades are meant to run.

Variants (each account picks one via accounts.strategy):
  "trend"      EMA 20/100 — 7-coin backtest PF 3.41, ~9 trades/year
  "trend_fast" EMA 15/80  — 7-coin backtest PF 2.31, ~13 trades/year, deeper drawdowns
"""

import pandas as pd

import config

EMA_FAST = getattr(config, "TREND_EMA_FAST", 20)
EMA_SLOW = getattr(config, "TREND_EMA_SLOW", 100)
ATR_LEN = 20
STOP_ATR = getattr(config, "TREND_STOP_ATR", 3.0)
TRIGGER = "trend_ema"
FAST_TRIGGER = "trend_ema_fast"

VARIANTS = {
    "trend":      {"fast": EMA_FAST, "slow": EMA_SLOW, "trigger": TRIGGER},
    "trend_fast": {"fast": getattr(config, "TREND_FAST_EMA_FAST", 15),
                   "slow": getattr(config, "TREND_FAST_EMA_SLOW", 80), "trigger": FAST_TRIGGER},
}
TRIGGERS = {v["trigger"]: k for k, v in VARIANTS.items()}  # trigger -> variant name


def _prep(df_d, fast, slow):
    df = df_d.copy()
    df["ema_f"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_s"] = df["close"].ewm(span=slow, adjust=False).mean()
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_LEN).mean()
    return df


def entry_signal(symbol, df_d, variant="trend"):
    """df_d: daily bars where the LAST row is the bar still forming (as returned by the bridge).
    Returns a setup dict (same keys bot.py/trade_logger expect) or None."""
    v = VARIANTS[variant]
    if df_d is None or len(df_d) < v["slow"] + 5:
        return None
    df = _prep(df_d, v["fast"], v["slow"])
    last, prev = df.iloc[-2], df.iloc[-3]          # last closed bar and the one before
    crossed_up = last["ema_f"] > last["ema_s"] and prev["ema_f"] <= prev["ema_s"]
    if not crossed_up or pd.isna(last["atr"]):
        return None
    entry = float(last["close"])
    sl_dist = STOP_ATR * float(last["atr"])
    return {
        "symbol": symbol, "direction": "bullish",
        "entry": entry, "sl": entry - sl_dist, "sl_dist": sl_dist,
        "tp": 0.0, "tp1": None, "tp2": 0.0, "rr": 0.0,
        "grade": "TREND", "score": 100,
        "met": [f"D1 EMA{v['fast']} crossed above EMA{v['slow']}", f"Stop {STOP_ATR}x ATR({ATR_LEN})"],
        "trigger": v["trigger"], "atr": float(last["atr"]),
        "w_bias": "-", "d_trend": "bullish", "h4_bias": "-", "synced": 0, "swing_eq": 0.0, "adx": 0.0,
    }


def manage(df_d, opened_at, current_sl, trigger=TRIGGER):
    """Returns ("exit", None) if the trend has ended, ("trail", new_sl) if the stop should be raised,
    or (None, None). `trigger` (stored with the trade) selects the EMA pair it was opened with."""
    v = VARIANTS[TRIGGERS.get(trigger, "trend")]
    if df_d is None or len(df_d) < v["slow"] + 5:
        return None, None
    df = _prep(df_d, v["fast"], v["slow"])
    closed = df.iloc[:-1]
    last = closed.iloc[-1]
    if last["ema_f"] < last["ema_s"]:
        return "exit", None
    since = closed[closed.index >= pd.Timestamp(opened_at).floor("1D")]
    if since.empty or pd.isna(last["atr"]):
        return None, None
    chandelier = float(since["close"].max()) - STOP_ATR * float(last["atr"])
    if chandelier > (current_sl or 0):
        return "trail", chandelier
    return None, None
