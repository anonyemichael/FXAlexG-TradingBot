"""
Multi-Account Manager for FXAlexG Bot.
Handles SQLite storage, credential obfuscation, risk settings, and account queries.
Enables self-hosted multi-account trading directly through Telegram.
"""

import os
import sqlite3
import base64
from datetime import datetime, timezone
import config

DB_PATH = os.path.join(os.path.dirname(__file__), "accounts.db")
SECRET_KEY = getattr(config, "TELEGRAM_TOKEN", "fxalexg_secret_salt")[:16].encode()


def _conn():
    return sqlite3.connect(DB_PATH)


def _obfuscate(text: str) -> str:
    """Lightweight reversible obfuscation for stored passwords."""
    if not text:
        return ""
    data = text.encode("utf-8")
    xor_bytes = bytes([b ^ SECRET_KEY[i % len(SECRET_KEY)] for i, b in enumerate(data)])
    return base64.b64encode(xor_bytes).decode("ascii")


def _deobfuscate(encoded: str) -> str:
    """Reverse obfuscation."""
    if not encoded:
        return ""
    try:
        xor_bytes = base64.b64decode(encoded.encode("ascii"))
        data = bytes([b ^ SECRET_KEY[i % len(SECRET_KEY)] for i, b in enumerate(xor_bytes)])
        return data.decode("utf-8")
    except Exception:
        return encoded


def init_db():
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            login INTEGER PRIMARY KEY,
            user_id TEXT,
            chat_id TEXT,
            label TEXT,
            password TEXT,
            server TEXT,
            risk_pct REAL DEFAULT 1.0,
            balance REAL DEFAULT 0.0,
            equity REAL DEFAULT 0.0,
            currency TEXT DEFAULT 'USD',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            last_checked TEXT
        )
    """)
    conn.commit()

    # Pre-populate master demo and real accounts from config if table is empty
    c.execute("SELECT COUNT(*) FROM accounts")
    count = c.fetchone()[0]
    if count == 0 and hasattr(config, "ACCOUNTS"):
        now_str = datetime.now(timezone.utc).isoformat()
        for key, acc in config.ACCOUNTS.items():
            login = acc.get("login")
            srv = acc.get("server")
            pw = acc.get("password", "")
            lbl = acc.get("label", key.title())
            if login and srv:
                c.execute("""
                    INSERT OR REPLACE INTO accounts 
                    (login, user_id, chat_id, label, password, server, risk_pct, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    int(login),
                    "master",
                    str(getattr(config, "TELEGRAM_CHAT_ID", "")),
                    lbl,
                    _obfuscate(pw),
                    srv,
                    getattr(config, "RISK_PERCENT", 1.0),
                    1,
                    now_str
                ))
        conn.commit()
    conn.close()


def add_or_update_account(login: int, server: str, password: str = "", label: str = "", 
                          user_id: str = "", chat_id: str = "", risk_pct: float = 1.0,
                          balance: float = 0.0, equity: float = 0.0, currency: str = "USD") -> dict:
    conn = _conn()
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()
    lbl = label or f"Account #{login}"

    c.execute("""
        INSERT INTO accounts (login, user_id, chat_id, label, password, server, risk_pct, balance, equity, currency, is_active, created_at, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(login) DO UPDATE SET
            server = excluded.server,
            password = CASE WHEN excluded.password != '' THEN excluded.password ELSE accounts.password END,
            label = excluded.label,
            user_id = excluded.user_id,
            chat_id = excluded.chat_id,
            risk_pct = excluded.risk_pct,
            balance = excluded.balance,
            equity = excluded.equity,
            currency = excluded.currency,
            is_active = 1,
            last_checked = excluded.last_checked
    """, (
        int(login),
        str(user_id),
        str(chat_id),
        lbl,
        _obfuscate(password) if password else "",
        str(server).strip(),
        float(risk_pct),
        float(balance),
        float(equity),
        currency,
        now_str,
        now_str
    ))
    conn.commit()
    conn.close()
    return get_account(login)


def get_account(login: int) -> dict:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE login = ?", (int(login),))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["password"] = _deobfuscate(d.get("password", ""))
    return d


def get_active_accounts() -> list:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE is_active = 1 ORDER BY login ASC")
    rows = c.fetchall()
    conn.close()
    res = []
    for r in rows:
        d = dict(r)
        d["password"] = _deobfuscate(d.get("password", ""))
        res.append(d)
    return res


def get_all_accounts() -> list:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts ORDER BY is_active DESC, login ASC")
    rows = c.fetchall()
    conn.close()
    res = []
    for r in rows:
        d = dict(r)
        d["password"] = _deobfuscate(d.get("password", ""))
        res.append(d)
    return res


def get_accounts_by_user(user_id: str) -> list:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE user_id = ? OR user_id = 'master' ORDER BY login ASC", (str(user_id),))
    rows = c.fetchall()
    conn.close()
    res = []
    for r in rows:
        d = dict(r)
        d["password"] = _deobfuscate(d.get("password", ""))
        res.append(d)
    return res


def toggle_account_status(login: int, active: bool = None) -> bool:
    conn = _conn()
    c = conn.cursor()
    if active is None:
        c.execute("UPDATE accounts SET is_active = 1 - is_active WHERE login = ?", (int(login),))
    else:
        c.execute("UPDATE accounts SET is_active = ? WHERE login = ?", (1 if active else 0, int(login)))
    conn.commit()
    conn.close()
    acc = get_account(login)
    return bool(acc and acc["is_active"])


def set_account_risk(login: int, risk_pct: float) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE accounts SET risk_pct = ? WHERE login = ?", (float(risk_pct), int(login)))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def update_account_balance(login: int, balance: float, equity: float, currency: str = "USD"):
    conn = _conn()
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()
    c.execute("""
        UPDATE accounts 
        SET balance = ?, equity = ?, currency = ?, last_checked = ? 
        WHERE login = ?
    """, (float(balance), float(equity), currency, now_str, int(login)))
    conn.commit()
    conn.close()


def delete_account(login: int) -> bool:
    conn = _conn()
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE login = ?", (int(login),))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# Initialize on import
init_db()
