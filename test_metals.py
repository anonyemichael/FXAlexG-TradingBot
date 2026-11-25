"""
Offline tests for metals_strategy.py (gold 1H breakout, both directions).
Run: python3 test_metals.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metals_strategy as MS  # noqa: E402

NOW = pd.Timestamp("2026-06-10 09:20", tz="UTC")


def h1(last_close=None, late=False, n=300, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=NOW.floor("1h"), periods=n, freq="1h", tz="UTC")       # last row still forming
    c = 3500 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    o = np.r_[3500, c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c}, idx)
    if last_close:
        j = n - 3 if late else n - 2                                                # breakout candle
        prev = df.iloc[j - MS.BREAK:j]
        lvl = prev["high"].max() if last_close == "up" else prev["low"].min()
        f = 1.004 if last_close == "up" else 0.996
        df.iloc[j:n - 1, df.columns.get_loc("close")] = lvl * f
        df.iloc[j:n - 1, df.columns.get_loc("high")] = np.maximum(df["high"].iloc[j:n - 1], lvl * f)
        df.iloc[j:n - 1, df.columns.get_loc("low")] = np.minimum(df["low"].iloc[j:n - 1], lvl * f)
    return df


def d1(up=True, n=300):
    idx = pd.date_range(end=NOW.floor("1D"), periods=n, freq="1D", tz="UTC")
    c = 3000 * np.exp(np.arange(n) * (0.002 if up else -0.002))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c}, idx)


def test_buy_in_uptrend_sell_in_downtrend():
    s = MS.entry_signal("XAUUSDm", h1("up"), d1(True), NOW)
    assert s and s["dir"] == 1 and s["sl_dist"] > 0 and "buys only" in s["met"][0]
    s = MS.entry_signal("XAUUSDm", h1("down"), d1(False), NOW)
    assert s and s["dir"] == -1 and "sells only" in s["met"][0] and "low" in s["met"][1]


def test_filter_blocks_wrong_direction():
    assert MS.entry_signal("XAUUSDm", h1("down"), d1(True), NOW) is None
    assert MS.entry_signal("XAUUSDm", h1("up"), d1(False), NOW) is None
    assert MS.entry_signal("XAUUSDm", h1(None), d1(True), NOW) is None


def test_one_candle_late_still_trades_if_price_is_beyond():
    s = MS.entry_signal("XAUUSDm", h1("up", late=True), d1(True), NOW)
    assert s and s["dir"] == 1


def test_forming_candle_is_ignored():
    df = h1(None)
    df.iloc[-1, df.columns.get_loc("close")] = df["high"].max() * 1.05            # only the forming candle breaks
    assert MS.entry_signal("XAUUSDm", df, d1(True), NOW) is None


def test_trailing_only_tightens_both_ways():
    df = h1(None)
    opened = df.index[-30]
    px = float(df["close"].iloc[-30])
    lvl = MS.manage(df, 1, opened, px * 0.95, px, NOW)
    assert lvl is not None and px * 0.95 < lvl < float(df["close"].iloc[-30:].max())
    assert MS.manage(df, 1, opened, lvl + 1000, px, NOW) is None                   # never loosened
    lvl = MS.manage(df, -1, opened, px * 1.05, px, NOW)
    assert lvl is not None and float(df["close"].iloc[-30:].min()) < lvl < px * 1.05
    assert MS.manage(df, -1, opened, lvl - 1000, px, NOW) is None


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
