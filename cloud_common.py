"""
Shared helpers for the GitHub-hosted bot (cloud_runner.py = trading, telegram_control.py = Telegram listener).

State lives in the GitHub Actions cache as two small JSON files, each written by ONE side only:
  cloud_state/runner.json   written by the trading run  (last run log, health, filter, processed requests)
  cloud_ctl/control.json    written by the listener     (chat, settings, MT5 login, pending requests)
Both are encrypted with a key derived from the Telegram bot token (a GitHub secret), so the cache of this
public repository never holds readable account data, logs or passwords.
"""

import base64
import hashlib
import json
import os

RUNNER_STATE = os.environ.get("CLOUD_STATE", os.path.join("cloud_state", "runner.json"))
CONTROL_STATE = os.environ.get("CLOUD_CONTROL", os.path.join("cloud_ctl", "control.json"))
DEFAULT_SERVER = "Exness-MT5Trial9"


def _fernet(token):
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("fxalexg-state:" + (token or "")).encode()).digest())
    return Fernet(key)


def load_json(path, token):
    """Returns {} if the file is missing or can't be decrypted (e.g. the bot token changed)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    try:
        if raw.startswith(b"enc:"):
            raw = _fernet(token).decrypt(raw[4:])
        return json.loads(raw)
    except Exception:
        return {}


def save_json(path, data, token):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    body = json.dumps(data).encode()
    if token:
        body = b"enc:" + _fernet(token).encrypt(body)
    with open(path, "wb") as f:
        f.write(body)


def tg_send(http, token, chat, text):
    if not token or not chat:
        return False
    try:
        r = http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


HELP = (
    "🛰️ <b>Momentum bot — Telegram control</b>\n"
    "<b>Instant</b> (answered within a minute):\n"
    "/settings — risk, coins, paused or not\n"
    "/pause · /resume — stop/allow new trades (open trades are still managed)\n"
    "/risk 3 — risk per trade in % (0.5–10)\n"
    "/log — what the last run did\n"
    "/health — last run, login, errors\n"
    "/filter — Bitcoin trend filter\n"
    "/quiet · /verbose — hourly check notes off/on\n"
    "/login ACCOUNT PASSWORD SERVER · /logout\n"
    "<b>Live from MT5</b> (starts a run, reply in ~3 min):\n"
    "/status — balance, open trades, P&amp;L\n"
    "/trades — last 10 closed trades\n"
    "/run — check for trades right now\n"
    "/testtrade — tiny trade opened and closed at once, to prove MT5 orders work\n"
    "/close SOLUSD — close one trade · /closeall confirm — close all"
)
