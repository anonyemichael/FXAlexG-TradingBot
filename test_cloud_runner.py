"""
Offline tests for cloud_runner.py with a fake MetaTrader5 module (no broker, no network).
Run: python3 test_cloud_runner.py
"""

import contextlib
import io
import os
import sys
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd

os.environ.setdefault("TELEGRAM_TOKEN", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cloud_runner as C  # noqa: E402

NOW = pd.Timestamp("2026-06-10 09:07", tz="UTC")


def _bars(n, end, freq, drift, seed, price):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    close = price * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    op = np.r_[price, close[:-1]]
    hi = np.maximum(op, close) * 1.002
    lo = np.minimum(op, close) * 0.998
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close}, index=idx)


class FakeMT5:
    TIMEFRAME_H4, TIMEFRAME_D1 = 16388, 16408
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1

    def __init__(self, btc_up=True, breakout=True):
        last4 = NOW.floor("4h") - pd.Timedelta(hours=4)          # last closed 4H candle (open time)
        self.h4 = {"SOLUSDm": _bars(300, last4 + pd.Timedelta(hours=4), "4h", 0.0, 1, 150.0)}
        if breakout:   # make the last CLOSED candle break the 42-candle high
            df = self.h4["SOLUSDm"]
            i = df.index.get_loc(last4)
            df.iloc[i, df.columns.get_loc("close")] = df["high"].iloc[i - 42:i].max() * 1.03
            df.iloc[i, df.columns.get_loc("high")] = df["close"].iloc[i] * 1.001
        d_end = NOW.floor("1D")
        self.d1 = {"BTCUSDm": _bars(300, d_end, "1D", 0.004 if btc_up else -0.004, 2, 60000.0),
                   "SOLUSDm": _bars(300, d_end, "1D", 0.0, 3, 150.0)}
        self.positions, self.deals, self.orders, self.mods, self.closes = [], [], [], [], []
        self.logged_in = False

    # connection
    def initialize(self, **kw):
        self.logged_in = kw.get("password") == "right"
        return self.logged_in

    def last_error(self): return (-6, "Authorization failed")
    def shutdown(self): pass
    def terminal_info(self): return NS(trade_allowed=True, connected=True)
    def account_info(self): return NS(balance=10.0, equity=10.0, login=123)

    # symbols
    def symbol_info(self, s):
        if s not in ("SOLUSDm", "BTCUSDm"):
            return None
        return NS(digits=3, point=0.001, spread=50, trade_tick_size=0.001, trade_tick_value=0.001,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01, filling_mode=1, trade_stops_level=0)

    def symbol_select(self, s, on): return True

    def symbol_info_tick(self, s):
        px = float((self.h4.get(s) if s in self.h4 else self.d1[s])["close"].iloc[-1])
        return NS(time=int(NOW.timestamp()), bid=px, ask=px * 1.0005)

    def copy_rates_from_pos(self, s, tf, start, n):
        df = (self.h4 if tf == self.TIMEFRAME_H4 else self.d1).get(s)
        if df is None:
            return None
        out = df.iloc[-n:].copy()
        out["time"] = ((out.index - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).astype(int)
        return out.reset_index(drop=True).to_records(index=False)

    # trading
    def positions_get(self): return list(self.positions)

    def history_deals_get(self, frm, to):
        return [d for d in self.deals if frm.timestamp() <= d.time <= to.timestamp()]

    def order_send(self, req):
        if req["action"] == self.TRADE_ACTION_DEAL and req["type"] == self.ORDER_TYPE_BUY:
            self.orders.append(req)
            t = int(NOW.timestamp())
            self.positions.append(NS(ticket=len(self.orders), symbol=req["symbol"], volume=req["volume"], sl=req["sl"],
                                     tp=0.0, magic=req["magic"], comment=req["comment"], time=t, profit=0.0))
            self.deals.append(NS(symbol=req["symbol"], entry=self.DEAL_ENTRY_IN, magic=req["magic"], time=t, profit=0.0))
        elif req["action"] == self.TRADE_ACTION_SLTP:
            self.mods.append(req)
        else:
            self.closes.append(req)
        return NS(retcode=self.TRADE_RETCODE_DONE, comment="done")


class FakeHTTP:
    def __init__(self): self.sent, self.updates = [], []

    def post(self, url, json=None, timeout=None):
        self.sent.append(json["text"]); return NS(status_code=200)

    def get(self, url, params=None, timeout=None):
        ups, self.updates = self.updates, []
        return NS(json=lambda: {"result": ups})


ENV = {"MT5_LOGIN": "123", "MT5_PASSWORD": "right", "MT5_SERVER": "Exness-MT5Trial9",
       "MOMENTUM_TELEGRAM_TOKEN": "tok", "MOMENTUM_TELEGRAM_CHAT_ID": "999", "MODE": "live", "MOMENTUM_RISK_PCT": "5"}


def _run(mt5, env=ENV, state=None, http=None):
    C.config.MOMENTUM_SYMBOLS = ["SOLUSDm", "ADAUSDm"]          # ADA doesn't exist on the fake broker
    C.config.MOMENTUM_ENGINES = ["mom_4h"]
    state = {} if state is None else state
    http = http or FakeHTTP()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = C.run(mt5, env, state, http=http, now=NOW)
    return code, state, http, buf.getvalue()


def test_buys_breakout_with_capped_risk_and_quiet_logs():
    mt5 = FakeMT5()
    code, state, http, out = _run(mt5)
    assert code == 0 and len(mt5.orders) == 1, (code, mt5.orders, http.sent)
    o = mt5.orders[0]
    assert o["symbol"] == "SOLUSDm" and o["magic"] == C.MAGIC and o["sl"] < o["price"] and o["tp"] == 0.0
    loss = (o["price"] - o["sl"]) / 0.001 * 0.001 * o["volume"]
    assert loss <= 10.0 * 0.08 + 0.01                        # never above the 8% cap on $10
    assert any("BUY SOLUSDm" in t for t in http.sent)
    for secret in ("123", "10.0", "SOLUSDm", str(round(o["price"], 2))):   # public log stays clean
        assert secret not in out, (secret, out)


def test_same_breakout_not_bought_twice():
    mt5 = FakeMT5()
    _run(mt5)
    mt5.positions.clear()                                    # stopped out within the same 4H candle
    _run(mt5)
    assert len(mt5.orders) == 1


def test_filter_off_no_buys_and_daily_exit():
    mt5 = FakeMT5(btc_up=False)
    mt5.positions.append(NS(ticket=7, symbol="SOLUSDm", volume=0.05, sl=100.0, tp=0.0, magic=C.MAGIC,
                            comment="MOM D1", time=int((NOW - pd.Timedelta(days=3)).timestamp()), profit=1.0))
    code, state, http, _ = _run(mt5)
    assert not mt5.orders and len(mt5.closes) == 1 and mt5.closes[0]["position"] == 7
    assert state["regime"] is False


def test_trailing_stop_raised():
    mt5 = FakeMT5(breakout=False)
    mt5.positions.append(NS(ticket=8, symbol="SOLUSDm", volume=0.05, sl=1.0, tp=0.0, magic=C.MAGIC,
                            comment="MOM 4H", time=int((NOW - pd.Timedelta(days=5)).timestamp()), profit=2.0))
    _run(mt5)
    assert len(mt5.mods) == 1 and mt5.mods[0]["sl"] > 1.0 and mt5.mods[0]["position"] == 8


def test_bad_login_alerts_once_per_6h():
    mt5 = FakeMT5()
    env = {**ENV, "MT5_PASSWORD": "wrong"}
    code, state, http, out = _run(mt5, env)
    assert code == 1 and any("login failed" in t for t in http.sent)
    n = len(http.sent)
    _run(mt5, env, state, http)
    assert len(http.sent) == n                               # no repeat within 6 hours
    assert "wrong" not in out


def test_dry_mode_never_trades_or_messages():
    mt5 = FakeMT5()
    code, state, http, out = _run(mt5, {**ENV, "MODE": "dry"})
    assert code == 0 and not mt5.orders and not http.sent and "1 new entry" in out


def test_missing_secrets_and_commands():
    code, state, http, _ = _run(FakeMT5(), {"MOMENTUM_TELEGRAM_TOKEN": "tok", "MOMENTUM_TELEGRAM_CHAT_ID": "999", "MODE": "live"})
    assert code == 0 and any("MT5_LOGIN" in t for t in http.sent)
    mt5 = FakeMT5(breakout=False)
    http = FakeHTTP()
    http.updates = [{"update_id": 5, "message": {"chat": {"id": 999}, "text": "/status"}},
                    {"update_id": 6, "message": {"chat": {"id": 555}, "text": "/status"}}]   # stranger ignored
    _, state, http, _ = _run(mt5, http=http)
    assert sum("status" in t.lower() for t in http.sent) == 2      # /status reply + first-run daily summary
    assert state["tg_offset"] == 7


def test_size_trade_rules():
    info = NS(trade_tick_size=0.001, trade_tick_value=0.001, volume_min=0.1, volume_max=100, volume_step=0.1)
    lot, risk, why = C.size_trade(info, 7.0, 10.0, 5.0, 8.0)      # min lot risks $0.70 = 7% -> allowed
    assert lot == 0.1 and abs(risk - 0.7) < 1e-9 and why is None
    lot, risk, why = C.size_trade(info, 14.0, 10.0, 5.0, 8.0)     # min lot risks 14% -> skipped
    assert lot is None and "cap" in why


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
