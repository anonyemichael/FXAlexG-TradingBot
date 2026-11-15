import re
"""
Autonomous AI Agent Trainer & Policy Evolver.
Enables the trading bot to analyze its own empirical performance, train its own execution policy,
and evolve into a more powerful, adaptive quantitative agent over time.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
import ai_engine
import config
import notifier

logger = logging.getLogger("ai_trainer")

DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")
POLICY_FILE = os.path.join(os.path.dirname(__file__), "agent_policy.json")


def _init_evolution_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS agent_evolution (
            generation INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at TEXT,
            total_trades INTEGER,
            win_rate REAL,
            net_pnl REAL,
            policy_json TEXT,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()


def load_agent_policy() -> dict:
    """Loads the active evolved agent policy, or generates default initial policy."""
    if os.path.exists(POLICY_FILE):
        try:
            with open(POLICY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load agent policy: {e}")

    return {
        "generation": 1,
        "name": "SMC Adaptive Foundation",
        "last_trained": datetime.now(timezone.utc).isoformat(),
        "min_confidence": 75,
        "sl_min_forex_pips": 12.0,
        "sl_min_crypto_pct": 0.8,
        "allowed_triggers": ["15m_choch"],
        "restricted_triggers": {
            "4h_engulfing": "Requires confirmed 15M structure displacement; 0% historical win rate"
        },
        "asset_rules": {
            "XAUUSDm": {"min_balance_required": 100.0, "status": "LOCKED_MICRO_BALANCE"},
            "Crypto": {"status": "PRIORITY_ACTIVE", "note": "Positive historical expectancy"},
            "Forex": {"status": "ACTIVE_WITH_WIDE_STOPS", "note": "Minimum 12-pip stop floor"}
        },
        "axioms": [
            "Never enter 4H engulfing candles raw without 15M liquidity sweep confirmation.",
            "Enforce minimum 12-pip stop buffer on Forex pairs to avoid spread stopouts.",
            "Lock Gold trading when account balance is below $100 to prevent margin liquidation."
        ]
    }


def save_agent_policy(policy: dict):
    with open(POLICY_FILE, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=2, ensure_ascii=False)


def get_performance_matrix() -> dict:
    """Calculates granular empirical metrics from SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT id, opened_at, closed_at, symbol, direction, trigger, lot, entry, sl, tp, result_usd, status FROM trades WHERE status='closed' ORDER BY id ASC")
    rows = c.fetchall()

    if not rows:
        conn.close()
        return {}

    total = len(rows)
    wins = [r for r in rows if r[10] is not None and r[10] > 0]
    losses = [r for r in rows if r[10] is not None and r[10] < 0]
    be = [r for r in rows if r[10] is not None and r[10] == 0]

    net_pnl = sum(r[10] for r in rows if r[10] is not None)
    win_rate = (len(wins) / total) * 100.0 if total > 0 else 0.0

    # Group by trigger
    c.execute("SELECT trigger, COUNT(id), SUM(CASE WHEN result_usd > 0 THEN 1 ELSE 0 END), SUM(result_usd) FROM trades WHERE status='closed' GROUP BY trigger")
    trigger_stats = {}
    for r in c.fetchall():
        trig, cnt, w, pnl = r
        trigger_stats[trig or "unknown"] = {
            "count": cnt,
            "wins": w,
            "losses": cnt - w,
            "win_rate": round((w / cnt) * 100.0, 1) if cnt > 0 else 0.0,
            "net_pnl": round(pnl or 0.0, 2)
        }

    # Group by asset class
    asset_stats = {"Crypto": {"trades": 0, "wins": 0, "pnl": 0.0}, "Forex": {"trades": 0, "wins": 0, "pnl": 0.0}, "Gold": {"trades": 0, "wins": 0, "pnl": 0.0}}
    for r in rows:
        sym = r[3]
        pnl = r[10] or 0.0
        cls = "Crypto" if any(k in sym for k in ("BTC", "ETH", "SOL")) else ("Gold" if "XAU" in sym else "Forex")
        asset_stats[cls]["trades"] += 1
        if pnl > 0:
            asset_stats[cls]["wins"] += 1
        asset_stats[cls]["pnl"] += pnl

    for k in asset_stats:
        cnt = asset_stats[k]["trades"]
        w = asset_stats[k]["wins"]
        asset_stats[k]["win_rate"] = round((w / cnt) * 100.0, 1) if cnt > 0 else 0.0
        asset_stats[k]["pnl"] = round(asset_stats[k]["pnl"], 2)

    conn.close()

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "break_even": len(be),
        "win_rate": round(win_rate, 1),
        "net_pnl": round(net_pnl, 2),
        "trigger_stats": trigger_stats,
        "asset_stats": asset_stats,
    }


def train_agent(notify: bool = True) -> dict:
    """
    Autonomous Training Cycle:
    1. Analyzes real trade database performance matrix.
    2. Feeds metrics into Gemini Meta-Learning prompt.
    3. Gemini synthesizes evolved agent policy & axioms.
    4. Updates agent_policy.json and saves evolution generation in SQLite.
    5. Dispatches Telegram evolutionary briefing.
    """
    _init_evolution_db()
    matrix = get_performance_matrix()
    if not matrix or matrix.get("total_trades", 0) < 3:
        logger.info("Not enough trade data to run evolutionary training cycle.")
        return load_agent_policy()

    current_policy = load_agent_policy()
    next_gen = current_policy.get("generation", 1) + 1

    prompt = f"""You are the Evolutionary Meta-Learning Architect of an autonomous quantitative trading bot.
Your mission is to analyze our bot's empirical trading history and EVOLVE its execution policy so the bot grows continuously smarter, eliminates costly failure modes, and maximizes expectancy.

EMPIRICAL PERFORMANCE MATRIX:
- Total Closed Trades: {matrix['total_trades']}
- Win Rate: {matrix['win_rate']}% (Wins: {matrix['wins']} | Losses: {matrix['losses']} | BE: {matrix['break_even']})
- Net Realized PnL: ${matrix['net_pnl']:.2f}
- Performance by Trigger Pattern:
{json.dumps(matrix['trigger_stats'], indent=2)}
- Performance by Asset Class:
{json.dumps(matrix['asset_stats'], indent=2)}

KEY KNOWN VULNERABILITIES:
1. 4H Engulfing has 0% win rate across 7 attempts (-$21.12 loss).
2. XAUUSDm (Gold) caused an account stop-out liquidation (-$17.32) because minimum lot (0.01) risk was too big for a $20 micro account.
3. 15M CHoCH stop loss was previously set at only 0.2*ATR (under 1 pip), causing early noise stop-outs in under 3 minutes.
4. Crypto (ETHUSDm) produced the only win (+$4.62) with positive expectancy.

Output ONLY a valid JSON object representing the evolved Agent Policy for Generation {next_gen}:
{{
  "generation": {next_gen},
  "name": "<Short descriptive name for this policy version>",
  "min_confidence": <integer between 70 and 85>,
  "sl_min_forex_pips": <float between 10.0 and 18.0>,
  "sl_min_crypto_pct": <float between 0.8 and 1.5>,
  "allowed_triggers": ["15m_choch", ...],
  "restricted_triggers": {{
     "4h_engulfing": "<strict rule requiring 15M CHoCH displacement before taking 4H setups>"
  }},
  "asset_rules": {{
     "XAUUSDm": {{"min_balance_required": 100.0, "status": "LOCKED_MICRO_BALANCE"}},
     "Crypto": {{"status": "PRIORITY_ACTIVE"}},
     "Forex": {{"status": "ACTIVE_WITH_WIDE_STOPS"}}
  }},
  "axioms": [
     "<3 to 4 crisp, non-negotiable operational trading laws learned from this data>"
  ],
  "evolution_summary": "<2-sentence summary of what this new generation improves over the last>"
}}
Return raw JSON only."""

    try:
        raw, provider = ai_engine.query_ai(prompt, "You are a quantitative hedge fund architect specializing in algorithmic trade policy optimization.", json_mode=True)
        clean = re.sub(r"^```json\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"```$", "", clean.strip(), flags=re.MULTILINE)
        evolved_policy = json.loads(clean)
        evolved_policy["last_trained"] = datetime.now(timezone.utc).isoformat()

        # Save to disk
        save_agent_policy(evolved_policy)

        # Log into SQLite evolution history
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO agent_evolution (generation, trained_at, total_trades, win_rate, net_pnl, policy_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            evolved_policy["generation"],
            evolved_policy["last_trained"],
            matrix["total_trades"],
            matrix["win_rate"],
            matrix["net_pnl"],
            json.dumps(evolved_policy),
            evolved_policy.get("evolution_summary", "")
        ))
        conn.commit()
        conn.close()

        logger.info(f"AI Agent evolved to Generation {evolved_policy['generation']} successfully!")

        if notify:
            axioms_disp = "\n".join(f"  • <i>\"{a}\"</i>" for a in evolved_policy.get("axioms", []))
            msg = (
                f"🧬 <b>AI AGENT EVOLVED — Generation {evolved_policy['generation']}</b>\n\n"
                f"<b>Policy Name:</b> {evolved_policy.get('name', 'Adaptive SMC')}\n"
                f"<b>Historical Basis:</b> {matrix['total_trades']} Trades | Net: ${matrix['net_pnl']:.2f}\n"
                f"<b>Min Confidence:</b> {evolved_policy.get('min_confidence', 75)}%\n"
                f"<b>Stop Loss Floor:</b> {evolved_policy.get('sl_min_forex_pips', 12)} pips (Forex) | {evolved_policy.get('sl_min_crypto_pct', 1.0)}% (Crypto)\n\n"
                f"<b>Core Evolved Axioms:</b>\n{axioms_disp}\n\n"
                f"📝 <i>{evolved_policy.get('evolution_summary', '')}</i>\n\n"
                f"<i>Powered by Google Gemini Meta-Learner</i>"
            )
            notifier.send(msg)

        return evolved_policy
    except Exception as e:
        logger.error(f"Agent training failed: {e}")
        return current_policy