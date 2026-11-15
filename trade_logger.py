import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "trades.db"


def _conn():
    return sqlite3.connect(str(DB_PATH))


def init_db():
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at TEXT,
            closed_at TEXT,
            symbol TEXT,
            direction TEXT,
            grade TEXT,
            score INTEGER,
            lot REAL,
            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,
            risk_usd REAL,
            result_usd REAL,
            ticket INTEGER,
            status TEXT DEFAULT 'open',
            tp1 REAL,
            tp2 REAL,
            partial_closed INTEGER DEFAULT 0,
            partial_profit REAL DEFAULT 0.0,
            trigger TEXT
        )
    """)
    # Ensure newer columns exist for existing databases
    for col in ("trigger TEXT", "login INTEGER"):
        try:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col}")
        except Exception:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT,
            symbol TEXT,
            grade TEXT,
            score INTEGER,
            direction TEXT,
            action TEXT
        )
    """)

    # ── Self-Learning Episodic Memory Table ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER,
            symbol TEXT,
            direction TEXT,
            trigger_type TEXT,
            outcome TEXT,
            pnl REAL,
            lesson TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_trade_opened(setup, lot, risk_usd, ticket, login=None):
    conn = _conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO trades (opened_at, symbol, direction, grade, score, lot, entry, sl, tp, rr, risk_usd, ticket, status, tp1, tp2, trigger, login)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)
    """, (
        now, setup["symbol"], setup["direction"], setup["grade"], setup["score"],
        lot, setup["entry"], setup["sl"], setup["tp"], setup["rr"], risk_usd, ticket,
        setup.get("tp1"), setup.get("tp2"), setup.get("trigger", ""), int(login) if login else None,
    ))
    conn.commit()
    conn.close()


def log_trade_closed(ticket, result_usd):
    conn = _conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE trades SET closed_at = ?, result_usd = ?, status = 'closed' WHERE ticket = ? AND status = 'open'",
              (now, result_usd, ticket))
    conn.commit()
    conn.close()


def log_partial_close(ticket, partial_profit):
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE trades SET partial_closed = 1, partial_profit = ? WHERE ticket = ?",
              (partial_profit, ticket))
    conn.commit()
    conn.close()


def is_partial_closed(ticket):
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT partial_closed FROM trades WHERE ticket = ?", (ticket,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0])


def log_scan(symbol, grade, score, direction, action):
    conn = _conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("INSERT INTO scans (scanned_at, symbol, grade, score, direction, action) VALUES (?, ?, ?, ?, ?, ?)",
              (now, symbol, grade, score, direction, action))
    conn.commit()
    conn.close()


def get_open_trade_tickets(login=None, is_master=True):
    """Open tickets for one MT5 account. Trades logged before the login column existed
    (login IS NULL) belong to the master account, so they are included only for the master."""
    conn = _conn()
    c = conn.cursor()
    if login is None:
        c.execute("SELECT ticket FROM trades WHERE status = 'open'")
    else:
        c.execute("SELECT ticket FROM trades WHERE status = 'open' AND (login = ? OR (login IS NULL AND ? = 1))",
                  (int(login), 1 if is_master else 0))
    tickets = [r[0] for r in c.fetchall()]
    conn.close()
    return tickets


def get_trade_by_ticket(ticket):
    conn = _conn()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE ticket = ?", (ticket,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_daily_trade_count(login=None):
    conn = _conn()
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if login is None:
        c.execute("SELECT COUNT(*) FROM trades WHERE opened_at LIKE ?", (f"{today}%",))
    else:
        c.execute("SELECT COUNT(*) FROM trades WHERE opened_at LIKE ? AND login = ?", (f"{today}%", int(login)))
    count = c.fetchone()[0]
    conn.close()
    return count


_KZ_HOURS = {"asian": (0, 4), "london": (7, 10), "new_york": (12, 15)}


def get_kill_zone_trade_count(kill_zone, login=None):
    """Returns number of trades opened inside this kill zone's hours today (optionally for one account)."""
    start_h, end_h = _KZ_HOURS.get(kill_zone, (0, 24))
    conn = _conn()
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if login is None:
        c.execute("SELECT opened_at FROM trades WHERE opened_at LIKE ?", (f"{today}%",))
    else:
        c.execute("SELECT opened_at FROM trades WHERE opened_at LIKE ? AND login = ?", (f"{today}%", int(login)))
    count = 0
    for (opened_at,) in c.fetchall():
        try:
            if start_h <= datetime.fromisoformat(opened_at).hour < end_h:
                count += 1
        except Exception:
            pass
    conn.close()
    return count


# ── v3.1 NEW: Re-Entry Cooldown Support ─────────────────────────────────────

def get_last_loss_time(symbol):
    """
    Returns the closed_at datetime of the most recent LOSING trade for this symbol.
    Returns None if no recent loss exists.
    Used by bot.py for re-entry cooldown logic.
    """
    conn = _conn()
    c = conn.cursor()
    c.execute("""
        SELECT closed_at FROM trades
        WHERE symbol = ? AND status = 'closed' AND result_usd < 0
        ORDER BY closed_at DESC
        LIMIT 1
    """, (symbol,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def is_in_cooldown(symbol, cooldown_hours):
    """
    Returns (True, hours_remaining) if the symbol is still in cooldown after a recent loss.
    Returns (False, 0) if no cooldown applies.
    """
    last_loss = get_last_loss_time(symbol)
    if last_loss is None:
        return False, 0
    now = datetime.now(timezone.utc)
    elapsed = (now - last_loss).total_seconds() / 3600.0
    if elapsed < cooldown_hours:
        remaining = round(cooldown_hours - elapsed, 1)
        return True, remaining
    return False, 0


# ── v3.4 NEW: Self-Learning Episodic Memory Support ────────────────────────

def log_trade_memory(ticket: int, symbol: str, direction: str, trigger_type: str, outcome: str, pnl: float, lesson: str):
    """
    Saves a crisp, self-learned rule derived from an actual trade outcome into SQLite.
    """
    if not lesson or len(lesson.strip()) < 5:
        return
    conn = _conn()
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO trade_memories (ticket, symbol, direction, trigger_type, outcome, pnl, lesson, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticket, symbol, direction, trigger_type or "", outcome, float(pnl), lesson.strip(), now))
    conn.commit()
    conn.close()


def get_relevant_memories(symbol: str, trigger_type: str = None, limit: int = 3):
    """
    Human-like Associative Recall:
    Retrieves ONLY the top relevant historical lessons for this exact symbol & pattern.
    Avoids cognitive overload by returning maximum `limit` actionable lessons.
    """
    conn = _conn()
    c = conn.cursor()
    memories = []

    # 1. Exact match for symbol and trigger pattern
    if trigger_type:
        c.execute("""
            SELECT outcome, pnl, lesson, trigger_type FROM trade_memories
            WHERE symbol = ? AND trigger_type = ?
            ORDER BY id DESC LIMIT ?
        """, (symbol, trigger_type, limit))
        for row in c.fetchall():
            memories.append({
                "outcome": row[0],
                "pnl": row[1],
                "lesson": row[2],
                "trigger": row[3],
            })

    # 2. If fewer than limit, supplement with most recent lessons on this symbol (prioritizing lessons from losses)
    if len(memories) < limit:
        needed = limit - len(memories)
        c.execute("""
            SELECT outcome, pnl, lesson, trigger_type FROM trade_memories
            WHERE symbol = ?
            ORDER BY id DESC LIMIT ?
        """, (symbol, needed + len(memories)))
        for row in c.fetchall():
            item = {
                "outcome": row[0],
                "pnl": row[1],
                "lesson": row[2],
                "trigger": row[3],
            }
            if not any(m["lesson"] == item["lesson"] for m in memories):
                memories.append(item)
                if len(memories) >= limit:
                    break

    conn.close()
    return memories


def stats_by_login(since_iso=None):
    """{login: {"closed", "wins", "losses", "net", "open", "best", "worst"}} for the scoreboard."""
    conn = _conn()
    c = conn.cursor()
    q = "SELECT login, status, result_usd FROM trades WHERE login IS NOT NULL"
    args = ()
    if since_iso:
        q += " AND opened_at >= ?"
        args = (since_iso,)
    out = {}
    for login, status, res in c.execute(q, args):
        s = out.setdefault(int(login), {"closed": 0, "wins": 0, "losses": 0, "net": 0.0, "open": 0, "best": 0.0, "worst": 0.0})
        if status == "open":
            s["open"] += 1
        elif res is not None:
            s["closed"] += 1
            s["net"] += res
            s["wins" if res > 0 else "losses"] += 1
            s["best"] = max(s["best"], res)
            s["worst"] = min(s["worst"], res)
    conn.close()
    return out


def get_recent_trades(limit=5):
    """Retrieves most recent closed trades for AI context."""
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT symbol, direction, result_usd, trigger FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"symbol": r[0], "direction": r[1], "result_usd": r[2], "trigger": r[3]} for r in rows]
