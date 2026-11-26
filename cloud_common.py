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
RUNNER_GOLD_STATE = os.path.join(os.path.dirname(RUNNER_STATE) or ".", "runner_gold.json")   # gold (cent) account
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


def gold_text(g, balance=None):
    """Telegram text about the gold engine from the trading run's health['gold'] (runner + /gold)."""
    tr = g.get("trend")
    if not tr:
        return "🥇 <b>Gold 1H</b>: trend not known yet."
    up = tr > 0
    txt = (f"🥇 <b>Gold 1H</b>: {'UP ⬆️ — buys only' if up else 'DOWN ⬇️ — sells only'}\n"
           f"Daily close {g['close']:,.2f} vs 200-day avg {g['ema']:,.2f}\n")
    if g.get("hi"):
        txt += (f"Next: {'BUY if a 1H candle closes above ' + format(g['hi'], ',.2f') if up else 'SELL if a 1H candle closes below ' + format(g['lo'], ',.2f')}"
                f" · stop ~{g['stop']:,.2f} away")
    pct = g.get("min_risk_pct") or (g["min_risk"] / balance * 100 if g.get("min_risk") and balance else None)
    if g.get("min_risk") and pct:
        txt += f"\nSmallest lot risks ${g['min_risk']:.2f} ({pct:.1f}% of balance)"
    return txt


HELP = (
    "🛰️ <b>Momentum bot — Telegram control</b>\n"
    "<b>Instant</b> (answered within a minute):\n"
    "/settings — risk, coins, paused or not\n"
    "/pause · /resume — stop/allow new trades (open trades are still managed)\n"
    "/risk 3 — risk per trade in % (0.5–10)\n"
    "/log — what the last run did\n"
    "/health — last run, login, errors\n"
    "/filter — Bitcoin trend filter\n"
    "/gold — gold trend, next buy/sell level, size check\n"
    "/goldrisk 2 — gold risk per trade in % (0.5–8, tested default 2)\n"
    "/quiet · /verbose — hourly check notes off/on\n"
    "/login ACCOUNT PASSWORD SERVER · /logout\n"    "/goldlogin ACCOUNT PASSWORD SERVER — separate account for gold (e.g. Standard Cent) · /goldlogout\n"
    "<b>Live from MT5</b> (starts a run, reply in ~3 min):\n"
    "/status — balance, open trades, P&amp;L\n"
    "/trades — last 10 closed trades\n"
    "/run — check for trades right now\n"
    "/testtrade — tiny trade opened and closed at once, to prove MT5 orders work\n"
    "/close SOLUSD or /close XAUUSD — close one trade · /closeall confirm — close all\n"
    "<b>Strategies</b> (day entries on 1H candles, winners held as swings): crypto 1H/4H/daily breakouts "
    "(buy only, while BTC trends up) + gold 1H breakouts (BUY and SELL)"
)
