"""
Separate Telegram bot for watching the Momentum Breakout strategy on its own.

Set MOMENTUM_TELEGRAM_TOKEN (and optionally MOMENTUM_TELEGRAM_CHAT_ID) in the VPS .env, or as GitHub secrets
so the deploy workflow writes them. With a token set:
  - every message about momentum accounts/trades goes to this bot instead of the main one
    (if this bot can't deliver, the main bot still gets it, so nothing is lost)
  - commands: /status  /trades  /filter  /help
  - a daily summary at MONITOR_DAILY_HOUR UTC
Without MOMENTUM_TELEGRAM_CHAT_ID, the first chat that sends /start to the bot becomes its owner;
messages from any other chat are ignored.
"""

import json
import os

import requests

import config

_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_FILE = os.path.join(_DIR, "monitor_chat_id.json")
OFFSET_FILE = os.path.join(_DIR, "monitor_tg_offset.json")
MOMENTUM_MARKERS = ("🛰️", "Momentum Breakout")


def token():
    return getattr(config, "MOMENTUM_TELEGRAM_TOKEN", "") or ""


def enabled():
    return bool(token())


def chat_id():
    cid = getattr(config, "MOMENTUM_TELEGRAM_CHAT_ID", None)
    if cid:
        return str(cid)
    try:
        with open(CHAT_FILE) as f:
            return str(json.load(f).get("chat_id") or "") or None
    except Exception:
        return None


def _save_chat(cid):
    with open(CHAT_FILE, "w") as f:
        json.dump({"chat_id": cid}, f)


def is_momentum_message(text):
    head = (text or "")[:200]
    return any(m in head for m in MOMENTUM_MARKERS)


def send(text, to=None):
    """Send via the monitor bot. Returns True if Telegram accepted it."""
    target = to or chat_id()
    if not enabled() or not target:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token()}/sendMessage",
                          json={"chat_id": target, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Monitor bot send error: {e}")
        return False


def _offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(json.load(f).get("offset", 0))
    except Exception:
        return 0


def _save_offset(o):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": o}, f)


HELP = ("🛰️ <b>Momentum Breakout monitor</b>\n"
        "/status — accounts, open trades, market filter\n"
        "/trades — last 10 momentum trades and P&L\n"
        "/filter — Bitcoin vs its 100-day EMA\n"
        "/help — this list")


def poll(handlers):
    """Read new messages and answer commands. handlers: {"/status": fn() -> str, ...}."""
    if not enabled():
        return
    params = {"timeout": 0}
    off = _offset()
    if off:
        params["offset"] = off
    try:
        data = requests.get(f"https://api.telegram.org/bot{token()}/getUpdates", params=params, timeout=10).json()
    except Exception:
        return
    for upd in data.get("result", []):
        _save_offset(upd["update_id"] + 1)
        msg = upd.get("message") or upd.get("edited_message") or {}
        cid = str((msg.get("chat") or {}).get("id") or "")
        text = (msg.get("text") or "").strip()
        if not cid or not text:
            continue
        owner = chat_id()
        if owner is None and text.lower().startswith("/start"):
            _save_chat(cid)
            owner = cid
            send("✅ This chat is now the Momentum Breakout monitor.\n\n" + HELP, to=cid)
            continue
        if cid != owner:
            continue   # never answer strangers
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/help"):
            send(HELP)
        elif cmd in handlers:
            try:
                send(handlers[cmd]())
            except Exception as e:
                send(f"⚠️ {cmd} failed: {e}")
