"""
Offline checks for the "momentum" method (no MT5 bridge, no Telegram, no network).
Run: python3 test_momentum.py
"""

import os
import sqlite3
import sys
import tempfile

import numpy as np
import pandas as pd

os.environ.setdefault("TELEGRAM_TOKEN", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import momentum_strategy as M  # noqa: E402


def _bars(n, start="2025-01-01", freq="4h", drift=0.0, seed=1, price=100.0, vol=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = price * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    op = np.r_[price, close[:-1]]
    hi = np.maximum(op, close) * (1 + rng.uniform(0, 0.004, n))
    lo = np.minimum(op, close) * (1 - rng.uniform(0, 0.004, n))
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close, "volume": 0.0}, index=idx)


def _btc(trend_up, n=400):
    return _bars(n, start="2024-01-01", freq="1D", drift=0.004 if trend_up else -0.004, seed=2, price=50000)


def _breakout(df):
    df = df.copy()
    df.iloc[-2, df.columns.get_loc("close")] = df["high"].iloc[-45:-2].max() * 1.02
    df.iloc[-2, df.columns.get_loc("high")] = df["close"].iloc[-2] * 1.001
    return df


def test_regime():
    up, down = _btc(True), _btc(False)
    now_up = up.index[-1] + pd.Timedelta(hours=25)
    now_dn = down.index[-1] + pd.Timedelta(hours=25)
    assert M.regime_ok(up, now_up) is True
    assert M.regime_ok(down, now_dn) is False
    assert M.regime_ok(None) is False


def test_entry_needs_regime():
    df = _breakout(_bars(300))
    now = df.index[-1] + pd.Timedelta(hours=1)
    s = M.entry_signal("SOLUSDm", df, "mom_4h", True, now=now)
    assert s and s["trigger"] == "mom_4h" and s["direction"] == "bullish" and s["tp"] == 0.0
    assert abs((s["entry"] - s["sl"]) - s["sl_dist"]) < 1e-9
    assert M.entry_signal("SOLUSDm", df, "mom_4h", False, now=now) is None


def test_live_matches_backtest_flags():
    df = _bars(1200, drift=0.0005, seed=5)
    flags, _, _ = M.entry_flags(df, "mom_4h")
    hits = [i for i in np.where(flags)[0] if i > 80][:25]
    assert hits
    for i in hits:
        cut = df.iloc[:i + 2]          # last row = forming candle
        assert M.entry_signal("X", cut, "mom_4h", True, now=cut.index[-1] + pd.Timedelta(hours=1)) is not None
    for i in [i for i in range(100, 500) if not flags[i]][:25]:
        cut = df.iloc[:i + 2]
        assert M.entry_signal("X", cut, "mom_4h", True, now=cut.index[-1] + pd.Timedelta(hours=1)) is None


def test_manage_trail_and_regime_exit():
    df = _breakout(_bars(300))
    s = M.entry_signal("SOLUSDm", df, "mom_4h", True, now=df.index[-1] + pd.Timedelta(hours=1))
    rally = _bars(40, start=str(df.index[-1] + pd.Timedelta(hours=4)), drift=0.01, seed=3, price=s["entry"])
    full = pd.concat([df, rally])
    now = full.index[-1] + pd.Timedelta(hours=1)
    act, lvl = M.manage(full, "mom_4h", df.index[-1], s["sl"], True, now=now)
    assert act == "trail" and lvl > s["sl"]
    assert M.manage(full, "mom_4h", df.index[-1], lvl + 1e6, True, now=now) == (None, None)
    # 4H engine does not exit on the market filter; the daily engine does
    assert M.manage(full, "mom_4h", df.index[-1], s["sl"], False, now=now)[0] == "trail"
    assert M.manage(full, "mom_d1", df.index[-1], s["sl"], False, now=now)[0] == "exit"


def test_backtest_respects_regime():
    df = _bars(1500, drift=0.0006, seed=9)
    on = np.ones(len(df), dtype=bool)
    off = np.zeros(len(df), dtype=bool)
    assert len(M.backtest_symbol("X", df, "mom_4h", on, 0.001)) > 0
    assert len(M.backtest_symbol("X", df, "mom_4h", off, 0.001)) == 0


def test_risk_cap():
    import risk_manager

    class FB:
        def get_symbol_info(self, s):
            return {"tick_size": 0.01, "tick_value": 0.01, "volume_min": 1.0, "volume_max": 1000.0, "volume_step": 1.0}
    risk_manager.bridge = FB()
    # 1 unit min lot, $100 coin, $14 stop (daily-engine size) on a $10 account = 140% -> must be rejected at 8%
    lot, *_rest, warn = risk_manager.calculate_lot_size("SOLUSDm", 14.0, 10.0, risk_pct=5.0, max_risk_pct=8.0)
    assert lot is None and "REJECTED" in warn
    # $0.40 stop: 1 unit risks $0.40 = 4% -> allowed
    lot, risk_usd, pct, _ = risk_manager.calculate_lot_size("SOLUSDm", 0.40, 10.0, risk_pct=5.0, max_risk_pct=8.0)
    assert lot == 1.0 and pct <= 8.0


def test_bot_offline():
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
                 "VALUES (222, 'demo', '', 'srv', 5.0, 10.0, 1, 'momentum')")
    conn.commit(); conn.close()

    df4 = _breakout(_bars(300))
    d1 = _bars(300, start="2025-01-01", freq="1D", seed=4)
    state = {"btc": _btc(True)}
    orders, modified, closed = [], [], []

    class FakeBridge:
        def get_account(self): return {"login": 222, "balance": 10.0, "equity": 10.0, "currency": "USD", "connected": True}
        def switch_account(self, *a, **k): return {"ok": True}
        def get_positions(self):
            return [{"ticket": 7, "symbol": "SOLUSDm", "sl": orders[0]["sl"], "volume": 0.1, "current_price": 1}] if orders else []
        def get_symbol_info(self, s): return {"spread": 5, "tick_size": 0.01, "tick_value": 0.01,
                                              "volume_min": 0.1, "volume_max": 1000.0, "volume_step": 0.1}
        def get_price(self, s): return float(df4["close"].iloc[-2])
        def place_order(self, **k): orders.append(k); return {"ticket": 7}
        def modify_sl(self, t, sl, new_tp=None): modified.append(sl); return {"ok": True}
        def close_position(self, t): closed.append(t); return {"ok": True}

    fb = FakeBridge()
    for name in ("get_account", "switch_account", "get_positions", "get_symbol_info", "get_price",
                 "place_order", "modify_sl", "close_position"):
        setattr(bot.bridge, name, getattr(fb, name))
    import risk_manager
    risk_manager.bridge = bot.bridge
    bot.dp.get_4h = lambda symbol, count=300: df4
    bot.dp.get_daily = lambda symbol, count=300: state["btc"] if symbol.startswith("BTC") else d1
    bot.notifier.send = lambda *a, **k: None
    bot.notifier.send_trade_opened_for_account = lambda *a, **k: None
    bot.config.MOMENTUM_SYMBOLS = ["SOLUSDm"]
    bot.config.MOMENTUM_ENGINES = ["mom_4h"]
    real = M.closed_bars
    fake_now = {"t": df4.index[-1] + pd.Timedelta(hours=1)}
    M.closed_bars = lambda d, h, now=None: real(d, h, fake_now["t"] if h < 24 else d.index[-1] + pd.Timedelta(hours=25))
    bot._expected_candle = lambda engine: str(df4.index[-2])
    try:
        bot._mom_regime_cache.update(t=0.0, on=None)
        bot.run_momentum_scan()
        assert len(orders) == 1, orders
        o = orders[0]
        assert o["side"] == "buy" and o["sl"] < df4["close"].iloc[-2] and not o["tp"]
        bot.run_momentum_scan()                      # same candle -> no duplicate
        assert len(orders) == 1
        t = trade_logger.get_trade_by_ticket(7)
        assert t and t["trigger"] == "mom_4h" and t["login"] == 222
        # risk on a $10 account stays under the 8% momentum cap
        assert t["risk_usd"] <= 10.0 * 0.08 + 1e-9

        # market filter off -> no new entries
        state["btc"] = _btc(False)
        bot._mom_regime_cache.update(t=0.0, on=None)
        bot._mom_scanned.clear()
        orders_before = len(orders)
        bot.run_momentum_scan()
        assert len(orders) == orders_before
    finally:
        M.closed_bars = real


def test_bot_fresh_server_no_accounts():
    """Fresh server, empty accounts.db: trade the bridge's own account at MOMENTUM_RISK_PCT, one per coin."""
    import trade_logger
    import account_manager
    tmp = tempfile.mkdtemp()
    trade_logger.DB_PATH = os.path.join(tmp, "trades.db")
    account_manager.DB_PATH = os.path.join(tmp, "accounts.db")
    import bot
    trade_logger.init_db()
    account_manager.init_db()
    df4 = _breakout(_bars(300))
    orders, sent, positions = [], [], []

    class FB:
        def get_account(self): return {"login": 333, "balance": 100.0, "equity": 100.0, "currency": "USD", "connected": True}
        def get_positions(self): return list(positions)
        def get_symbol_info(self, s): return {"spread": 5, "tick_size": 0.01, "tick_value": 0.01,
                                              "volume_min": 0.01, "volume_max": 1000.0, "volume_step": 0.01}
        def get_price(self, s): return float(df4["close"].iloc[-2])
        def place_order(self, **k):
            orders.append(k); positions.append({"ticket": 9, "symbol": k["symbol"], "sl": k["sl"], "volume": k["lot"]})
            return {"ticket": 9}
    fb = FB()
    for name in ("get_account", "get_positions", "get_symbol_info", "get_price", "place_order"):
        setattr(bot.bridge, name, getattr(fb, name))
    import risk_manager
    risk_manager.bridge = bot.bridge
    bot.dp.get_4h = lambda symbol, count=300: df4
    bot.dp.get_daily = lambda symbol, count=300: _btc(True)
    bot.notifier.send = lambda *a, **k: sent.append(a[0])
    bot.config.MOMENTUM_SYMBOLS = ["SOLUSDm"]
    bot.config.MOMENTUM_ENGINES = ["mom_4h"]
    real = M.closed_bars
    M.closed_bars = lambda d, h, now=None: real(d, h, df4.index[-1] + pd.Timedelta(hours=1) if h < 24 else d.index[-1] + pd.Timedelta(hours=25))
    bot._expected_candle = lambda engine: str(df4.index[-2])
    try:
        bot._mom_regime_cache.update(t=0.0, on=None)
        bot._mom_scanned.clear()
        bot.run_momentum_scan()
        assert len(orders) == 1
        t = trade_logger.get_trade_by_ticket(9)
        assert t["login"] == 333 and t["risk_usd"] <= 100.0 * bot.config.MOMENTUM_RISK_PCT / 100 + 1e-9
        assert any(m.startswith("🛰️") for m in sent)          # routed to the monitor bot
        bot._mom_scanned.clear()
        bot.run_momentum_scan()                                   # same coin already held -> no second order
        assert len(orders) == 1
    finally:
        M.closed_bars = real


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
