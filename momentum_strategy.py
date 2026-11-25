"""
Crypto Momentum Breakout — account method "momentum".

Built from the research in MOMENTUM_RESEARCH.md: of every family tested (Alex G Set & Forget, SMC, grid bots,
DCA bots, dip-buying, weekly rotation, machine learning), plain trend-following breakouts on crypto, switched
off when Bitcoin is in a downtrend, were the only thing that held up on coins and years they were not tuned on.

  Market filter  Bitcoin's last daily close > its EMA(100). Off -> no new buys, and daily-engine trades close.
  4H engine      the 4H candle closes above the highest high of the previous 42 candles (last week's high)
                 -> buy. Stop 2.5 x ATR(20) on 4H, trailed after every 4H close (highest close - 2.5 ATR).
                 Stops are ~4-5% of price, small enough for a ~$10 account.
  Daily engine   the daily candle closes above the previous 20 days' high -> buy. Stop 3 x ATR(20) daily,
                 trailed every day. Stops are ~14% of price, so on small accounts the risk manager skips
                 these until the balance can carry the minimum lot.
  Long only, no fixed take-profit, one trade per coin.

Every function here is used by both the bot and momentum_research.py, so the backtest is the live code.
"""

import numpy as np
import pandas as pd

import config

REGIME_EMA = getattr(config, "MOMENTUM_REGIME_EMA", 100)
ENGINES = {
    "mom_4h": {"tf_hours": 4, "break": getattr(config, "MOMENTUM_4H_BREAK", 42),
               "atr_mult": getattr(config, "MOMENTUM_4H_ATR", 2.5), "regime_exit": False, "label": "4H"},
    "mom_d1": {"tf_hours": 24, "break": getattr(config, "MOMENTUM_D1_BREAK", 20),
               "atr_mult": getattr(config, "MOMENTUM_D1_ATR", 3.0), "regime_exit": True, "label": "Daily"},
}
TRIGGERS = set(ENGINES)
ATR_LEN = 20


def closed_bars(df, tf_hours, now=None):
    """Drop candles that have not closed yet (the bridge's last row is usually still forming)."""
    if df is None or df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    return df[(idx + pd.Timedelta(hours=tf_hours)) <= now]


def atr(df, n=ATR_LEN):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def regime_series(btc_daily_closed):
    """Bool Series by day: True where longs are allowed DURING that day (based on the previous day's close)."""
    c = btc_daily_closed["close"]
    ok = (c > c.ewm(span=REGIME_EMA, adjust=False).mean()).astype(float)
    return ok


def regime_ok(btc_daily, now=None):
    """Live: is the market filter on right now? Needs >= ~150 daily BTC candles."""
    if btc_daily is None or len(btc_daily) < REGIME_EMA // 2:
        return False
    closed = closed_bars(btc_daily, 24, now)
    if closed.empty:
        return False
    return bool(regime_series(closed).iloc[-1] > 0)


def regime_on_index(index, btc_daily_closed, engine):
    """Backtest: market-filter value as the bot would see it when each candle in `index` closes.
    4H candles inside day D only know BTC's close of day D-1; a daily candle of day D closes at the same
    moment as BTC's day D, so the daily engine uses that day's value."""
    ok = regime_series(btc_daily_closed)
    if ENGINES[engine]["tf_hours"] < 24:
        ok = ok.shift(1)
    return ok.reindex(index.floor("1D")).fillna(0).to_numpy() > 0


def entry_flags(df_closed, engine):
    """Bool array: True on each closed candle whose close breaks the engine's channel."""
    e = ENGINES[engine]
    brk = df_closed["high"].rolling(e["break"]).max().shift(1)
    a = atr(df_closed)
    return ((df_closed["close"] > brk) & a.notna()).to_numpy(), brk.to_numpy(), a.to_numpy()


def entry_signal(symbol, df, engine, regime_is_on, now=None):
    """Live entry check on the bridge bars for one engine. Returns a setup dict or None."""
    e = ENGINES[engine]
    if not regime_is_on or df is None or len(df) < e["break"] + ATR_LEN + 5:
        return None
    closed = closed_bars(df, e["tf_hours"], now)
    if len(closed) < e["break"] + ATR_LEN + 2:
        return None
    flags, brk, a = entry_flags(closed, engine)
    if not flags[-1]:
        return None
    entry = float(closed["close"].iloc[-1])
    sl_dist = e["atr_mult"] * float(a[-1])
    return {
        "symbol": symbol, "direction": "bullish",
        "entry": entry, "sl": entry - sl_dist, "sl_dist": sl_dist,
        "tp": 0.0, "tp1": None, "tp2": 0.0, "rr": 0.0,
        "grade": "MOM", "score": 100,
        "met": [f"BTC above its {REGIME_EMA}-day EMA (market filter on)",
                f"{e['label']} close above the {e['break']}-candle high ({float(brk[-1]):.6g})",
                f"Stop {e['atr_mult']}x ATR({ATR_LEN}) = {sl_dist / entry * 100:.1f}%, trailed every {e['label']} close"],
        "trigger": engine, "atr": float(a[-1]),
        "w_bias": "-", "d_trend": "-", "h4_bias": "bullish", "synced": 0, "swing_eq": 0.0, "adx": 0.0,
        "signal_time": str(closed.index[-1]),
    }


def trail_level(df_closed, opened_at, engine):
    e = ENGINES[engine]
    a = atr(df_closed)
    since = df_closed[df_closed.index >= pd.Timestamp(opened_at)]
    if since.empty or pd.isna(a.iloc[-1]):
        return None
    return float(since["close"].max()) - e["atr_mult"] * float(a.iloc[-1])


def manage(df, engine, opened_at, current_sl, regime_is_on, now=None):
    """Returns ("exit", None), ("trail", new_sl) or (None, None)."""
    e = ENGINES[engine]
    if e["regime_exit"] and not regime_is_on:
        return "exit", None
    if df is None or len(df) < ATR_LEN + 5:
        return None, None
    opened = pd.Timestamp(opened_at)
    if opened.tzinfo is None:
        opened = opened.tz_localize("UTC")
    closed = closed_bars(df, e["tf_hours"], now)
    lvl = trail_level(closed, opened.floor(pd.Timedelta(hours=e["tf_hours"])), engine)
    if lvl is not None and lvl > (current_sl or 0):
        return "trail", lvl
    return None, None


def backtest_symbol(sym, df, engine, regime_flags, spread_pct, swap_pct_day=0.15 / 365):
    """Same rules as live, on historical closed bars. Signal on close i, fill at open i+1 (+spread);
    stop checked on each later candle's low; stop trailed after each candle that doesn't stop out;
    daily engine also exits at the next open when the market filter turns off."""
    e = ENGINES[engine]
    flags, _, a = entry_flags(df, engine)
    o, l, c = (df[x].to_numpy() for x in ("open", "low", "close"))
    out, pos = [], None
    for i in range(e["break"] + ATR_LEN, len(df) - 1):
        if pos:
            hit = l[i] <= pos["stop"]
            if hit or (e["regime_exit"] and not regime_flags[i]):
                px = min(o[i], pos["stop"]) if hit else o[i + 1]
                t_exit = df.index[i] if hit else df.index[i + 1]
                days = (t_exit - pos["t"]).total_seconds() / 86400
                r = (px - pos["fill"]) / pos["risk"] - pos["fill"] / pos["risk"] * swap_pct_day * days
                out.append({"sym": sym, "engine": engine, "entry_t": pos["t"], "exit_t": t_exit, "r": r,
                            "days": days, "stop_pct": pos["risk"] / pos["fill"] * 100})
                pos = None
            else:
                pos["best"] = max(pos["best"], c[i])
                pos["stop"] = max(pos["stop"], pos["best"] - e["atr_mult"] * a[i])
            continue
        if flags[i] and regime_flags[i]:
            fill = o[i + 1] * (1 + spread_pct)
            stop = fill - e["atr_mult"] * a[i]
            if fill > stop:
                pos = {"fill": fill, "stop": stop, "risk": fill - stop, "best": fill, "t": df.index[i + 1]}
    return out
