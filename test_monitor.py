"""
Offline checks for the separate momentum monitor bot (monitor_bot.py). No network: requests is faked.
Run: python3 test_monitor.py
"""

import os
import sys
import tempfile

os.environ.setdefault("TELEGRAM_TOKEN", "main-token")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import monitor_bot  # noqa: E402
import notifier  # noqa: E402


class FakeResp:
    def __init__(self, data=None, code=200):
        self._d, self.status_code = data or {}, code

    def json(self):
        return self._d


class FakeTelegram:
    def __init__(self):
        self.sent, self.updates = [], []

    def post(self, url, json=None, timeout=None):
        self.sent.append((url.split("/bot")[1].split("/")[0], json["chat_id"], json["text"]))
        return FakeResp({"ok": True})

    def get(self, url, params=None, timeout=None):
        ups, self.updates = self.updates, []
        return FakeResp({"result": ups})


def _setup(tmp):
    fake = FakeTelegram()
    monitor_bot.requests = fake
    notifier.requests = fake
    monitor_bot.CHAT_FILE = os.path.join(tmp, "monitor_chat_id.json")
    monitor_bot.OFFSET_FILE = os.path.join(tmp, "monitor_tg_offset.json")
    notifier.CHAT_ID_FILE = os.path.join(tmp, "chat_id.json")
    config.TELEGRAM_TOKEN = "main-token"
    config.TELEGRAM_CHAT_ID = "111"
    config.MOMENTUM_TELEGRAM_TOKEN = "mon-token"
    config.MOMENTUM_TELEGRAM_CHAT_ID = None
    return fake


def _msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


def test_routing_and_ownership():
    fake = _setup(tempfile.mkdtemp())
    # no owner yet: momentum message falls back to the main bot, nothing is lost
    notifier.send("<b>🛰️ Momentum Breakout · #1</b>\nOPENED BUY SOLUSDm")
    assert fake.sent[-1][0] == "main-token"
    # first /start claims the monitor bot
    fake.updates = [_msg(1, 999, "/start")]
    monitor_bot.poll({})
    assert monitor_bot.chat_id() == "999" and fake.sent[-1][:2] == ("mon-token", "999")
    # now momentum messages go to the monitor bot, everything else to the main bot
    notifier.send("<b>🛰️ Momentum Breakout · #1</b>\nOPENED BUY SOLUSDm")
    assert fake.sent[-1][:2] == ("mon-token", "999")
    notifier.send("<b>📈 Crypto Trend · #2</b>\nOPENED BUY XRPUSDm")
    assert fake.sent[-1][:2] == ("main-token", "111")
    # a stranger is ignored, the owner gets answers
    n = len(fake.sent)
    fake.updates = [_msg(2, 555, "/status"), _msg(3, 555, "/start")]
    monitor_bot.poll({"/status": lambda: "secret balance"})
    assert len(fake.sent) == n and monitor_bot.chat_id() == "999"
    fake.updates = [_msg(4, 999, "/status")]
    monitor_bot.poll({"/status": lambda: "STATUS OK"})
    assert fake.sent[-1] == ("mon-token", "999", "STATUS OK")


def test_fixed_chat_id_and_disabled():
    fake = _setup(tempfile.mkdtemp())
    config.MOMENTUM_TELEGRAM_CHAT_ID = "777"
    fake.updates = [_msg(1, 888, "/start")]          # can't claim when the chat id is fixed
    monitor_bot.poll({})
    assert monitor_bot.chat_id() == "777"
    assert all(s[1] != 888 for s in fake.sent)
    config.MOMENTUM_TELEGRAM_TOKEN = ""                 # no token: everything stays on the main bot
    notifier.send("🛰️ Momentum market filter ON")
    assert fake.sent[-1][0] == "main-token"


def test_bot_status_texts():
    import trade_logger
    import account_manager
    tmp = tempfile.mkdtemp()
    trade_logger.DB_PATH = os.path.join(tmp, "trades.db")
    account_manager.DB_PATH = os.path.join(tmp, "accounts.db")
    import bot
    trade_logger.init_db(); account_manager.init_db()
    trade_logger.log_trade_opened({"symbol": "SOLUSDm", "direction": "bullish", "grade": "MOM", "score": 100,
                                   "entry": 150.0, "sl": 143.0, "tp": 0.0, "rr": 0.0, "trigger": "mom_4h"},
                                  0.1, 0.70, 42, login=222)
    import numpy as np
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=200, freq="1D", tz="UTC")
    btc = pd.DataFrame({"open": 60000.0, "high": 61000.0, "low": 59000.0,
                        "close": np.linspace(80000, 60000, 200)}, index=idx)
    bot.dp.get_daily = lambda s, count=300: btc
    txt = bot._monitor_status_text()
    assert "SOLUSDm" in txt and "Open trades: 1" in txt and "OFF" in txt
    assert "SOLUSDm" in bot._monitor_trades_text()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
