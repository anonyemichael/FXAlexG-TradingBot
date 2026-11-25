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


def _bars(n, end, freq, drift, seed, price, sd=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=end, periods=n, freq=freq, tz="UTC")
    close = price * np.exp(np.cumsum(rng.normal(drift, sd, n)))
    op = np.r_[price, close[:-1]]
    hi = np.maximum(op, close) * 1.002
    lo = np.minimum(op, close) * 0.998
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": close}, index=idx)


class FakeMT5:
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 16385, 16388, 16408
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1

    def __init__(self, btc_up=True, breakout=True, gold=None, balance=10.0):
        """gold: None (no gold symbol), 'up' (uptrend + 1H breakout), 'down' (downtrend + 1H breakdown),
        'against' (uptrend but a 1H breakdown), 'quiet' (uptrend, no breakout)."""
        self.balance = balance
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
        self.h1 = {}
        self.gold = gold
        if gold:
            last1 = NOW.floor("1h") - pd.Timedelta(hours=1)          # last closed 1H candle
            g = _bars(400, last1 + pd.Timedelta(hours=1), "1h", 0.0, 4, 3500.0, sd=0.002)
            i = g.index.get_loc(last1)
            if gold in ("up", "down", "against"):
                if gold == "up":
                    g.iloc[i, g.columns.get_loc("close")] = g["high"].iloc[i - 120:i].max() * 1.003
                    g.iloc[i, g.columns.get_loc("high")] = g["close"].iloc[i] * 1.0005
                else:
                    g.iloc[i, g.columns.get_loc("close")] = g["low"].iloc[i - 120:i].min() * 0.997
                    g.iloc[i, g.columns.get_loc("low")] = g["close"].iloc[i] * 0.9995
            self.h1["XAUUSDm"] = g
            self.d1["XAUUSDm"] = _bars(300, d_end, "1D", -0.003 if gold == "down" else 0.003, 5, 3000.0)

    # connection
    def initialize(self, **kw):
        self.logged_in = kw.get("password") == "right"
        return self.logged_in

    def last_error(self): return (-6, "Authorization failed")
    def shutdown(self): pass
    def terminal_info(self): return NS(trade_allowed=True, connected=True)
    def account_info(self): return NS(balance=self.balance, equity=self.balance, login=123)

    # symbols
    def symbol_info(self, s):
        if s == "XAUUSDm" and self.gold:            # Exness Standard: 1 lot = 100 oz, 0.01 lot minimum
            return NS(digits=2, point=0.01, spread=30, trade_tick_size=0.01, trade_tick_value=1.0, trade_mode=4,
                      volume_min=0.01, volume_max=200.0, volume_step=0.01, filling_mode=1, trade_stops_level=0,
                      trade_contract_size=100.0)
        if s not in ("SOLUSDm", "BTCUSDm"):
            return None
        return NS(digits=3, point=0.001, spread=50, trade_tick_size=0.001, trade_tick_value=0.001,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01, filling_mode=1, trade_stops_level=0)

    def symbol_select(self, s, on): return True

    def symbol_info_tick(self, s):
        src = self.h1 if s in self.h1 else self.h4 if s in self.h4 else self.d1
        px = float(src[s]["close"].iloc[-1])
        return NS(time=int(NOW.timestamp()), bid=px, ask=px * 1.0005)

    def copy_rates_from_pos(self, s, tf, start, n):
        df = {self.TIMEFRAME_H1: self.h1, self.TIMEFRAME_H4: self.h4}.get(tf, self.d1).get(s)
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
        if req["action"] == self.TRADE_ACTION_DEAL and "position" not in req:      # open (buy or sell)
            self.orders.append(req)
            t = int(NOW.timestamp())
            self.positions.append(NS(ticket=len(self.orders), symbol=req["symbol"], volume=req["volume"], sl=req["sl"],
                                     tp=0.0, magic=req["magic"], comment=req["comment"], time=t, profit=0.0,
                                     type=req["type"], price_open=req["price"]))
            self.deals.append(NS(symbol=req["symbol"], entry=self.DEAL_ENTRY_IN, magic=req["magic"], time=t, profit=0.0))
        elif req["action"] == self.TRADE_ACTION_SLTP:
            self.mods.append(req)
        else:
            self.closes.append(req)
            self.positions = [p for p in self.positions if p.ticket != req.get("position")]
        return NS(retcode=self.TRADE_RETCODE_DONE, comment="done")


class FakeHTTP:
    def __init__(self): self.sent, self.updates, self.deleted = [], [], []

    def post(self, url, json=None, timeout=None):
        if url.endswith("/deleteMessage"):
            self.deleted.append(json["message_id"])
        else:
            self.sent.append(json["text"])
        return NS(status_code=200)

    def get(self, url, params=None, timeout=None):
        ups, self.updates = self.updates, []
        return NS(json=lambda: {"result": ups})


ENV = {"MT5_LOGIN": "123", "MT5_PASSWORD": "right", "MT5_SERVER": "Exness-MT5Trial9",
       "MOMENTUM_TELEGRAM_TOKEN": "tok", "MOMENTUM_TELEGRAM_CHAT_ID": "999", "MODE": "live", "MOMENTUM_RISK_PCT": "5"}
NO_SECRETS = {"MOMENTUM_TELEGRAM_TOKEN": "tok", "MOMENTUM_TELEGRAM_CHAT_ID": "999", "MODE": "live"}


def _run(mt5, env=ENV, state=None, control=None, http=None):
    C.config.MOMENTUM_SYMBOLS = ["SOLUSDm", "ADAUSDm"]          # ADA doesn't exist on the fake broker
    C.config.MOMENTUM_TELEGRAM_CHAT_ID = None
    C.config.MOMENTUM_ENGINES = ["mom_4h"]
    state = {} if state is None else state
    http = http or FakeHTTP()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = C.run(mt5, env, state, control or {}, http=http, now=NOW)
    return code, state, http, buf.getvalue()


def test_buys_breakout_with_capped_risk_and_quiet_public_log():
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
    assert any("BUY SOLUSDm" in line for line in state["log"])            # private log (/log) has details
    assert state["health"]["login"] == "ok" and state["health"]["filter"]["on"] is True


def test_same_breakout_not_bought_twice():
    mt5 = FakeMT5()
    _, state, _, _ = _run(mt5)
    mt5.positions.clear()                                    # stopped out within the same 4H candle
    _run(mt5, state=state)
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
    assert code == 1 and any("login failed" in t for t in http.sent) and "wrong" not in out
    n = len(http.sent)
    _run(mt5, env, state, http=http)
    assert len(http.sent) == n                               # no repeat within 6 hours
    assert "FAILED" in state["health"]["login"]


def test_dry_mode_never_trades_or_messages():
    mt5 = FakeMT5()
    code, state, http, out = _run(mt5, {**ENV, "MODE": "dry"}, control={"requests": [{"id": 1, "cmd": "status", "arg": "", "ts": 0}]})
    assert code == 0 and not mt5.orders and not http.sent and "1 new entry" in out
    assert set(state) == {"last_dry_run"}                       # the real trading state is left alone


def test_login_from_telegram_control_and_ask_once():
    mt5 = FakeMT5()
    code, state, http, _ = _run(mt5, NO_SECRETS)
    assert code == 0 and not mt5.logged_in and any("/login" in t for t in http.sent)
    n = len(http.sent)
    _run(mt5, NO_SECRETS, state, http=http)
    assert len(http.sent) == n                               # asked only once
    control = {"creds": {"login": "123", "password": "right", "server": "Exness-MT5Trial9"}}
    code, state, http, out = _run(mt5, NO_SECRETS, state, control, http)
    assert code == 0 and mt5.logged_in and len(mt5.orders) == 1
    assert any("Logged in to MT5" in t for t in http.sent) and "right" not in out


def test_pause_and_risk_from_telegram():
    mt5 = FakeMT5()
    _run(mt5, control={"paused": True})
    assert not mt5.orders
    _run(mt5, control={"risk_pct": 1.0})
    o = mt5.orders[0]
    loss = (o["price"] - o["sl"]) * o["volume"]
    assert loss <= 10.0 * 0.01 + 0.01 or o["volume"] == 0.01   # 1% (or the broker minimum within the cap)


def test_telegram_requests_answered_once():
    mt5 = FakeMT5(breakout=False)
    mt5.positions.append(NS(ticket=9, symbol="SOLUSDm", volume=0.05, sl=1.0, tp=0.0, magic=C.MAGIC,
                            comment="MOM 4H", time=int((NOW - pd.Timedelta(days=1)).timestamp()), profit=0.5))
    control = {"requests": [{"id": 1, "cmd": "status", "arg": "", "ts": 0},
                            {"id": 2, "cmd": "close", "arg": "SOLUSD", "ts": 0},
                            {"id": 3, "cmd": "run", "arg": "", "ts": 0}]}
    code, state, http, _ = _run(mt5, control=control)
    assert len(mt5.closes) == 1 and mt5.closes[0]["position"] == 9
    assert any("Momentum bot — status" in t for t in http.sent) and any("Closed SOLUSDm" in t for t in http.sent)
    assert any("Check done" in t for t in http.sent) and state["done"] == [1, 2, 3]
    n = len(http.sent)
    _run(mt5, state=state, control=control, http=http)      # same requests again -> ignored
    assert not any("status" in t.lower() and "Daily" not in t for t in http.sent[n:])


def test_testtrade_opens_and_closes_smallest_position():
    mt5 = FakeMT5(breakout=False)
    control = {"requests": [{"id": 5, "cmd": "testtrade", "arg": "", "ts": 0}]}
    code, state, http, out = _run(mt5, control=control)
    assert len(mt5.orders) == 1 and mt5.orders[0]["magic"] == C.MAGIC_TEST and mt5.orders[0]["volume"] == 0.01
    assert len(mt5.closes) == 1 and mt5.closes[0]["magic"] == C.MAGIC_TEST
    assert any("MT5 test trade worked" in t for t in http.sent) and 5 in state["done"]
    assert not C.Broker(mt5).positions()                    # test trade never counts as a momentum trade


class ReadOnlyMT5(FakeMT5):
    """Logged in with the investor password: every order is refused with 10017."""
    def account_info(self): return NS(balance=10.0, equity=10.0, login=123, trade_allowed=False, trade_expert=True)

    def order_send(self, req):
        self.orders.append(req)
        return NS(retcode=10017, comment="Trade disabled")


class FxMT5(FakeMT5):
    """SOL can't be bought on this account; EURUSD can."""
    def symbol_info(self, s):
        if s == "EURUSDm":
            return NS(digits=5, point=0.00001, spread=10, trade_tick_size=0.00001, trade_tick_value=1.0, trade_mode=4,
                      volume_min=0.01, volume_max=100.0, volume_step=0.01, filling_mode=1, trade_stops_level=0)
        info = super().symbol_info(s)
        if info and s == "SOLUSDm":
            info.trade_mode = 0
        return info

    def symbol_info_tick(self, s):
        return NS(time=int(NOW.timestamp()), bid=1.05, ask=1.05001) if s == "EURUSDm" else super().symbol_info_tick(s)


def test_testtrade_readonly_login_is_explained():
    mt5 = ReadOnlyMT5(breakout=False)
    control = {"requests": [{"id": 7, "cmd": "testtrade", "arg": "", "ts": 0}]}
    _, state, http, out = _run(mt5, control=control)
    msg = next(t for t in http.sent if "Test trade failed" in t)
    assert "read-only" in msg and "investor" in msg and "This login may trade: NO" in msg
    assert "login may trade: NO" in out and "Algo Trading ON" in out and "rejected (code 10017)" in out
    assert "123" not in out                                      # public log: still no account number
    assert any("read-only" in t for t in http.sent if "Test trade" not in t)   # one-time alert too
    assert "read-only" in state["health"]["trading"] and not mt5.closes


def test_testtrade_skips_blocked_coin_and_falls_back_to_eurusd():
    mt5 = FxMT5(breakout=False)
    control = {"requests": [{"id": 8, "cmd": "testtrade", "arg": "", "ts": 0}]}
    _, _, http, _ = _run(mt5, control=control)
    assert [o["symbol"] for o in mt5.orders] == ["EURUSDm"] and len(mt5.closes) == 1
    msg = next(t for t in http.sent if "test trade worked" in t)
    assert "EURUSDm" in msg and "SOLUSDm (disabled)" in msg


def test_breakout_on_blocked_coin_is_not_bought():
    mt5 = FxMT5()
    _, state, _, _ = _run(mt5)
    assert not mt5.orders and any("disabled on this account" in l for l in state["log"])


def test_gold_buys_breakout_in_uptrend():
    mt5 = FakeMT5(breakout=False, gold="up", balance=5000.0)
    _, state, http, out = _run(mt5)
    o = [x for x in mt5.orders if x["symbol"] == "XAUUSDm"]
    assert len(o) == 1 and o[0]["type"] == mt5.ORDER_TYPE_BUY and o[0]["magic"] == C.MAGIC_METAL
    assert o[0]["sl"] < o[0]["price"] and o[0]["volume"] >= 0.01
    msg = next(t for t in http.sent if "BUY XAUUSDm" in t)
    assert "Gold 1H breakout" in msg and "Why:" in msg and "200-day" in msg and "trails up" in msg
    assert "XAU" not in out and "3,5" not in out                      # public log stays generic
    assert state["health"]["gold"]["trend"] == 1


def test_gold_sells_breakdown_in_downtrend_and_trails_down():
    mt5 = FakeMT5(breakout=False, gold="down", balance=5000.0)
    _, state, http, _ = _run(mt5)
    o = [x for x in mt5.orders if x["symbol"] == "XAUUSDm"]
    assert len(o) == 1 and o[0]["type"] == mt5.ORDER_TYPE_SELL and o[0]["sl"] > o[0]["price"]
    assert any("SELL XAUUSDm" in t and "breakdown" in t and "trails down" in t for t in http.sent)
    # next run: the stop of the open SELL is only ever lowered; /close XAUUSD buys it back at the ask
    pos = mt5.positions[0]
    pos.sl = o[0]["price"] * 1.05                                     # loose stop -> must be tightened
    pos.time = int((NOW - pd.Timedelta(hours=5)).timestamp())
    control = {"requests": [{"id": 3, "cmd": "close", "arg": "xauusd", "ts": 0}]}
    _, _, http, _ = _run(mt5, state={"last_run": NOW.timestamp()}, control=control)
    assert mt5.closes and mt5.closes[0]["type"] == mt5.ORDER_TYPE_BUY and mt5.closes[0]["position"] == pos.ticket
    assert any("Closed XAUUSDm" in t for t in http.sent)


def test_gold_trailing_stop_for_open_sell():
    mt5 = FakeMT5(breakout=False, gold="quiet", balance=5000.0)
    px = float(mt5.h1["XAUUSDm"]["close"].iloc[-1])
    mt5.positions.append(NS(ticket=77, symbol="XAUUSDm", volume=0.02, sl=px * 1.05, tp=0.0, magic=C.MAGIC_METAL,
                            comment="MET 1H", time=int((NOW - pd.Timedelta(hours=10)).timestamp()), profit=1.0,
                            type=1, price_open=px * 1.01))
    _, _, http, _ = _run(mt5)
    assert mt5.mods and mt5.mods[0]["sl"] < px * 1.05 and mt5.mods[0]["sl"] > px
    assert any("SELL: stop lowered" in t for t in http.sent)


def test_gold_trend_filter_blocks_the_wrong_side():
    mt5 = FakeMT5(breakout=False, gold="against", balance=5000.0)
    _, _, _, _ = _run(mt5)
    assert not [x for x in mt5.orders if x["symbol"] == "XAUUSDm"]


def test_gold_skipped_once_when_account_too_small():
    mt5 = FakeMT5(breakout=False, gold="up", balance=50.0)
    _, state, http, _ = _run(mt5)
    assert not mt5.orders and any("Gold BUY signal skipped" in t and "Cent" in t for t in http.sent)
    _, _, http, _ = _run(mt5, state=state)
    assert not any("signal skipped" in t for t in http.sent)               # not repeated every hour
    mt5 = FakeMT5(breakout=False, gold="up", balance=1000.0)              # min lot ~5.6% > 5% cap -> skipped
    _run(mt5)
    assert not mt5.orders
    _run(mt5, control={"gold_risk": 8})                                   # /goldrisk 8 raises the cap -> taken
    assert [o["volume"] for o in mt5.orders if o["symbol"] == "XAUUSDm"] == [0.01]


def test_hourly_note_and_quiet():
    env = {**ENV, "GITHUB_EVENT_NAME": "schedule"}
    _, _, http, _ = _run(FakeMT5(btc_up=False), env)
    assert any("UTC check" in t and "BTC filter off" in t for t in http.sent)
    _, _, http, _ = _run(FakeMT5(btc_up=False), env, control={"verbose": False})
    assert not any("UTC check" in t for t in http.sent)
    _, _, http, _ = _run(FakeMT5(btc_up=False), {**ENV, "HOURLY_CHECK": "true"})
    assert any("UTC check" in t for t in http.sent)                 # hourly run started by the listener
    _, _, http, _ = _run(FakeMT5(btc_up=False))
    assert not any("UTC check" in t for t in http.sent)             # on-demand runs don't add the note


def test_buy_message_explains_why():
    _, _, http, _ = _run(FakeMT5())
    buy = next(t for t in http.sent if "BUY SOLUSDm" in t)
    assert "Why:" in buy and "42-candle high" in buy and "BTC above" in buy


def test_state_is_encrypted_at_rest():
    import tempfile
    from cloud_common import load_json, save_json
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    save_json(path, {"creds": {"password": "Secret@1"}, "log": ["BUY SOLUSDm"]}, "tok")
    raw = open(path, "rb").read()
    assert b"Secret" not in raw and b"SOLUSD" not in raw and raw.startswith(b"enc:")
    assert load_json(path, "tok")["creds"]["password"] == "Secret@1"
    assert load_json(path, "other-token") == {}


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
