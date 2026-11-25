"""
Offline tests for telegram_control.py (fake Telegram + GitHub APIs).
Run: python3 test_telegram_control.py
"""

import os
import sys
from types import SimpleNamespace as NS

os.environ.setdefault("TELEGRAM_TOKEN", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_control as T  # noqa: E402

NOW = 1_800_000_000.0
ENV = {"MOMENTUM_TELEGRAM_TOKEN": "tok", "GITHUB_TOKEN": "gh", "GITHUB_REPOSITORY": "me/repo"}


class FakeHTTP:
    def __init__(self, updates=None, dispatch_status=204):
        self.updates, self.sent, self.deleted, self.dispatches = list(updates or []), [], [], []
        self.dispatch_status = dispatch_status

    def get(self, url, params=None, timeout=None):
        if url.endswith("/getMe"):
            return NS(json=lambda: {"ok": True, "result": {"username": "my_trading_bot"}})
        if url.endswith("/getWebhookInfo"):
            return NS(json=lambda: {"ok": True, "result": {"url": "", "pending_update_count": 0}})
        ups, self.updates = self.updates, []
        return NS(json=lambda: {"ok": True, "result": ups})

    def post(self, url, json=None, headers=None, timeout=None):
        if "api.github.com" in url:
            self.dispatches.append((url, json, headers))
            return NS(status_code=self.dispatch_status)
        if url.endswith("/deleteMessage"):
            self.deleted.append(json["message_id"])
        else:
            self.sent.append((json["chat_id"], json["text"]))
        return NS(status_code=200)


def msg(uid, chat, text, mid=None):
    return {"update_id": uid, "message": {"message_id": mid or uid, "chat": {"id": chat}, "text": text}}


def _go(updates, ctl=None, run=None, env=ENV, now=NOW, http=None, owner=None, hello=False):
    T.config.MOMENTUM_TELEGRAM_CHAT_ID = owner          # tests choose: no fixed owner unless given
    ctl = {} if ctl is None else ctl
    if not hello:
        ctl.setdefault("hello_sent", True)
        ctl.setdefault("auto_test_done", True)
    http = http or FakeHTTP(updates)
    if updates and http.updates == []:
        http.updates = list(updates)
    changed = T.handle(http, env, ctl, run or {}, now)
    return changed, ctl, http


def texts(http):
    return [t for _, t in http.sent]


def test_hello_to_owner_once():
    _, ctl, http = _go([], {}, owner="6220850396", hello=True)
    assert http.sent and http.sent[0][0] == "6220850396" and "Momentum bot connected" in http.sent[0][1]
    assert [r["cmd"] for r in ctl["requests"]] == ["testtrade"] and http.dispatches   # one-time test trade
    assert any("test trade" in t for _, t in http.sent)
    assert ctl["hello_sent"] and ctl["tg_chat"] == "6220850396"
    _, ctl, http = _go([msg(1, 555, "/start")], ctl, owner="6220850396")   # stranger can't take over
    assert not http.sent and ctl["tg_chat"] == "6220850396"


def test_claim_and_ignore_strangers():
    changed, ctl, http = _go([msg(1, 777, "/start"), msg(2, 555, "/pause"), msg(3, 555, "/start")])
    assert changed and ctl["tg_chat"] == "777" and not ctl.get("paused")
    assert all(chat == "777" for chat, _ in http.sent) and "Telegram control" in texts(http)[0]
    assert ctl["tg_offset"] == 4


def test_login_saves_deletes_and_starts_run():
    _, ctl, http = _go([msg(1, 777, "/login 436015341 Pass@1 Exness-MT5Trial9", mid=42)])
    assert ctl["creds"] == {"login": "436015341", "password": "Pass@1", "server": "Exness-MT5Trial9"}
    assert http.deleted == [42]
    assert len(http.dispatches) == 1
    url, body, headers = http.dispatches[0]
    assert url.endswith("/repos/me/repo/actions/workflows/cloud-bot.yml/dispatches")
    assert body == {"ref": "main", "inputs": {"mode": "live"}} and headers["Authorization"] == "Bearer gh"
    assert ctl["requests"][0]["cmd"] == "run" and "Pass@1" not in " ".join(texts(http))
    _, ctl, http = _go([msg(2, 777, "/login nope")], ctl)
    assert any("Send it like this" in t for t in texts(http))


def test_pause_resume_risk_settings():
    ctl = {"tg_chat": "777"}
    _go([msg(1, 777, "/pause")], ctl)
    assert ctl["paused"] is True
    _, ctl, http = _go([msg(2, 777, "/risk 3"), msg(3, 777, "/risk 50"), msg(4, 777, "/settings")], ctl)
    assert ctl["risk_pct"] == 3.0
    t = texts(http)
    assert "3%" in t[0] and "0.5 to 10" in t[1] and "PAUSED" in t[2] and "3%" in t[2]
    _go([msg(5, 777, "/resume")], ctl)
    assert ctl["paused"] is False


def test_log_health_filter_from_last_run():
    run = {"last_run": NOW - 600, "result": "ok, 0 new trade(s)", "log": ["09:07 BTC filter OFF", "09:07 SOLUSDm 4H: no breakout"],
           "health": {"login": "ok", "algo": "on", "open": 0, "balance": "$10.00",
                      "filter": {"on": False, "btc": 59828.0, "ema": 74236.0}}}
    _, _, http = _go([msg(1, 777, "/log"), msg(2, 777, "/health"), msg(3, 777, "/filter")], {"tg_chat": "777"}, run)
    log, health, filt = texts(http)
    assert "10 min ago" in log and "no breakout" in log
    assert "MT5 login: ok" in health and "$10.00" in health
    assert "OFF" in filt and "74,236" in filt and "-19.4%" in filt


def test_live_commands_queue_and_dispatch_once_a_minute():
    ctl = {"tg_chat": "777"}
    _, ctl, http = _go([msg(1, 777, "/status"), msg(2, 777, "/trades")], ctl)
    assert [r["cmd"] for r in ctl["requests"]] == ["status", "trades"] and len(http.dispatches) == 1
    _, ctl, http = _go([msg(3, 777, "/run")], ctl, now=NOW + 30)
    assert len(http.dispatches) == 0                       # a run is already starting
    _, ctl, http = _go([msg(4, 777, "/closeall")], ctl, now=NOW + 40)
    assert "closeall" not in [r["cmd"] for r in ctl["requests"]] and "confirm" in texts(http)[0]
    _, ctl, http = _go([msg(5, 777, "/closeall confirm"), msg(6, 777, "/close sol")], ctl, now=NOW + 120)
    assert [r["cmd"] for r in ctl["requests"]][-2:] == ["closeall", "close"] and ctl["requests"][-1]["arg"] == "SOL"


def test_finished_requests_are_dropped_and_no_change_is_quiet():
    ctl = {"tg_chat": "777", "bot_name": "my_trading_bot", "requests": [{"id": 1, "cmd": "status", "arg": "", "ts": NOW}], "next_id": 1,
           "last_dispatch": NOW}
    changed, ctl, http = _go([], ctl, {"done": [1]})
    assert changed and ctl["requests"] == []
    changed, ctl, http = _go([], ctl, {"done": [1]})
    assert changed is False and not http.sent and not http.dispatches


def test_dispatch_failure_is_reported():
    http = FakeHTTP([msg(1, 777, "/status")], dispatch_status=403)
    _, ctl, http = _go([], {"tg_chat": "777"}, http=http)
    assert any("Couldn't start a run" in t for t in texts(http))


def test_quiet_verbose_and_testtrade():
    _, ctl, http = _go([msg(1, 777, "/quiet")], {"tg_chat": "777"})
    assert ctl["verbose"] is False and "off" in texts(http)[0]
    _, ctl, http = _go([msg(2, 777, "/verbose"), msg(3, 777, "/testtrade")], ctl, now=NOW + 300)
    assert ctl["verbose"] is True and ctl["requests"][-1]["cmd"] == "testtrade" and http.dispatches


def test_main_saves_before_dispatch():
    import tempfile
    from cloud_common import load_json
    d = tempfile.mkdtemp()
    T.CONTROL_STATE, T.RUNNER_STATE = os.path.join(d, "ctl.json"), os.path.join(d, "run.json")
    out = os.path.join(d, "out.txt")
    T.config.MOMENTUM_TELEGRAM_CHAT_ID = "777"
    fake = FakeHTTP([msg(1, 777, "/login 436015341 Pass@1 Exness-MT5Trial9", mid=9)])
    T.requests = fake
    old = dict(os.environ)
    os.environ.update({"MOMENTUM_TELEGRAM_TOKEN": "tok", "LISTEN_SECONDS": "0", "GITHUB_OUTPUT": out})
    try:
        T.main()
    finally:
        os.environ.clear(); os.environ.update(old)
    outputs = open(out).read()
    assert "changed=true" in outputs and "dispatch=true" in outputs
    assert not fake.dispatches                      # the workflow starts the run, after saving the cache
    saved = load_json(T.CONTROL_STATE, "tok")
    assert saved["creds"]["login"] == "436015341" and b"Pass@1" not in open(T.CONTROL_STATE, "rb").read()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed")
