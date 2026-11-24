"""
Offline checks for the swing4h and alexg methods (no MT5 bridge, no Telegram, no network).
Run: python3 test_swing_alexg.py
"""

import os
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

os.environ.setdefault("TELEGRAM_TOKEN", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alexg_strategy as A  # noqa: E402
import swing_strategy as W  # noqa: E402


def _bars(n, start="2026-01-01", drift=0.0, seed=1, freq="4h", price=1.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rets = rng.normal(drift, 0.01, n)
    close = price * np.exp(np.cumsum(rets))
    op = np.r_[price, close[:-1]]
    hi = np.maximum(op, close) * (1 + rng.uniform(0, 0.004, n))
    lo = np.minimum(op, close) * (1 - rng.uniform(0, 0.004, n))
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 0.0}, index=idx)


def test_closed_bars():
    df = _bars(10)
    now = df.index[-1] + pd.Timedelta(hours=1)          # last candle still forming
    assert len(A.closed_bars(df, 4, now)) == 9
    now = df.index[-1] + pd.Timedelta(hours=4)          # last candle just closed
    assert len(A.closed_bars(df, 4, now)) == 10


def test_swing_signal_and_trail():
    df = _bars(300, drift=0.0)
    # force a clean breakout on the last closed candle
    df.iloc[-2, df.columns.get_loc("close")] = df["high"].iloc[-45:-2].max() * 1.02
    df.iloc[-2, df.columns.get_loc("high")] = df["close"].iloc[-2] * 1.001
    now = df.index[-1] + pd.Timedelta(hours=1)
    s = W.entry_signal("SOLUSDm", df, now=now)
    assert s and s["direction"] == "bullish" and s["trigger"] == "swing4h"
    assert abs((s["entry"] - s["sl"]) - s["sl_dist"]) < 1e-9 and s["sl_dist"] > 0
    assert s["tp"] == 0.0 and s["tp2"] == 0.0                 # no fixed target
    # trailing: after a rally the stop must move up, never down
    up = _bars(40, start=str(df.index[-1] + pd.Timedelta(hours=4)), drift=0.01, seed=3, price=s["entry"])
    full = pd.concat([df, up])
    act, lvl = W.manage(full, df.index[-1], s["sl"], now=full.index[-1] + pd.Timedelta(hours=1))
    assert act == "trail" and lvl > s["sl"]
    act2, _ = W.manage(full, df.index[-1], lvl + 1.0, now=full.index[-1] + pd.Timedelta(hours=1))
    assert act2 is None


def test_swing_backtest_matches_live():
    """Every live entry_signal on a closed candle must equal a backtest signal on that candle."""
    df = _bars(900, drift=0.0004, seed=5)
    sig, _ = W.signals(df)
    hits = list(np.where(sig.to_numpy())[0])
    assert hits, "synthetic data should produce breakouts"
    for i in hits[:20]:
        live = W.entry_signal("XRPUSDm", df.iloc[:i + 2], now=df.index[i + 1] + pd.Timedelta(hours=1))
        assert live is not None, i
    misses = [i for i in range(100, 400) if not sig.iloc[i]][:20]
    for i in misses:
        assert W.entry_signal("XRPUSDm", df.iloc[:i + 2], now=df.index[i + 1] + pd.Timedelta(hours=1)) is None


def test_alexg_signal_shape():
    df = _bars(2400, drift=0.0003, seed=11, price=1.10)
    sigs = A.compute_signals(df)
    assert isinstance(sigs, list)
    for s in sigs[:50]:
        d = s["direction"]
        assert (s["entry"] - s["sl"]) * d > 0, "stop must be on the losing side"
        assert abs((s["tp"] - s["entry"]) - d * A.DEFAULTS["rr"] * s["risk"]) < 1e-9
        assert s["sync"] >= A.DEFAULTS["min_sync"]
    if sigs:   # the live wrapper returns the same setup on that candle
        s = sigs[-1]
        cut = df.iloc[:s["i"] + 2]
        live = A.entry_signal("EURUSDm", cut, now=cut.index[-1] + pd.Timedelta(hours=1))
        assert live and live["trigger"] == "alexg_sf" and abs(live["entry"] - s["entry"]) < 1e-12


def test_bot_dispatch_offline():
    """run_swing_scan -> _dispatch_setup_to_accounts -> place_order, with a fake bridge and temp DBs."""
    import config
    import trade_logger
    import account_manager
    tmp = tempfile.mkdtemp()
    trade_logger.DB_PATH = os.path.join(tmp, "trades.db")
    account_manager.DB_PATH = os.path.join(tmp, "accounts.db")
    import bot
    trade_logger.init_db()
    account_manager.init_db()
    conn = sqlite3.connect(account_manager.DB_PATH)
    conn.execute("INSERT INTO accounts (login, label, password, server, risk_pct, balance, is_active, strategy) "
                 "VALUES (111, 'demo', '', 'srv', 5.0, 10.0, 1, 'swing4h')")
    conn.commit(); conn.close()

    df = _bars(300)
    df.iloc[-2, df.columns.get_loc("close")] = df["high"].iloc[-45:-2].max() * 1.02
    orders, sent = [], []

    class FakeBridge:
        def get_account(self): return {"login": 111, "balance": 10.0, "equity": 10.0, "currency": "USD", "connected": True}
        def switch_account(self, *a, **k): return {"ok": True}
        def get_positions(self): return []
        def get_symbol_info(self, s): return {"spread": 10, "tick_size": 0.0001, "tick_value": 0.0001,
                                              "volume_min": 1, "volume_max": 10000, "volume_step": 1}
        def get_price(self, s): return float(df["close"].iloc[-2])
        def place_order(self, **k): orders.append(k); return {"ticket": 42}
        def spread_ok(self, s): return True

    fb = FakeBridge()
    for name in ("get_account", "switch_account", "get_positions", "get_symbol_info", "get_price",
                 "place_order", "spread_ok"):
        setattr(bot.bridge, name, getattr(fb, name))
    import risk_manager
    risk_manager.bridge = bot.bridge
    bot.dp.get_4h = lambda symbol, count=300: df
    bot.notifier.send = lambda *a, **k: sent.append(a)
    bot.notifier.send_trade_opened_for_account = lambda *a, **k: sent.append(a)
    bot.config.SWING_SYMBOLS = ["SOLUSDm"]
    now_fn = bot._last_closed_4h
    bot._last_closed_4h = lambda: str(df.index[-2])
    real_closed = A.closed_bars
    bot.alexg_strategy.closed_bars = lambda d, h=4, now=None: real_closed(d, h, d.index[-1] + pd.Timedelta(hours=1))
    W.closed_bars = bot.alexg_strategy.closed_bars
    try:
        bot.run_swing_scan()
        assert len(orders) == 1, orders
        o = orders[0]
        assert o["side"] == "buy" and o["sl"] < bot.bridge.get_price("SOLUSDm") and not o["tp"]
        bot.run_swing_scan()                        # same candle again -> no second order
        assert len(orders) == 1
        t = trade_logger.get_trade_by_ticket(42)
        assert t and t["trigger"] == "swing4h" and t["login"] == 111
    finally:
        bot._last_closed_4h = now_fn
        bot.alexg_strategy.closed_bars = real_closed
        W.closed_bars = real_closed


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
