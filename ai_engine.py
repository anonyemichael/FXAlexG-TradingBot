from __future__ import annotations

import json
import logging
import os
import re
import time
import requests
from datetime import datetime, timezone

import config

logger = logging.getLogger("ai_engine")

# ── API Configurations (100% Google Gemini Native Engine) ──────────
GEMINI_MODEL = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
TIMEOUT = getattr(config, "AI_TIMEOUT", 45)
MAX_RETRIES = getattr(config, "AI_MAX_RETRIES", 3)

# Persistent Chat Memory File
CHAT_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "chat_memory.json")


# ── Persistent Chat Memory ──────────────────────────────────────────

def load_chat_history(max_turns: int = 12) -> list[dict]:
    """Loads recent multi-turn conversation history."""
    if os.path.exists(CHAT_MEMORY_FILE):
        try:
            with open(CHAT_MEMORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
                if isinstance(history, list):
                    return history[-max_turns:]
        except Exception as e:
            logger.warning(f"Could not load chat memory: {e}")
    return []


def save_chat_turn(role: str, text: str):
    """Saves a conversation turn ('user' or 'model') to persistent memory."""
    try:
        history = []
        if os.path.exists(CHAT_MEMORY_FILE):
            try:
                with open(CHAT_MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        history = data
            except Exception:
                history = []
        history.append({
            "role": "user" if role == "user" else "model",
            "text": text,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        })
        history = history[-24:]
        with open(CHAT_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not save chat turn: {e}")


def clear_chat_history():
    """Wipes chat history for a fresh session."""
    try:
        if os.path.exists(CHAT_MEMORY_FILE):
            os.remove(CHAT_MEMORY_FILE)
    except Exception as e:
        logger.warning(f"Could not clear chat memory: {e}")


def get_market_context_str(bot_status: dict = None) -> str:
    """Computes comprehensive live market context with live account state & open positions."""
    now_utc = datetime.now(timezone.utc)
    day_name = now_utc.strftime("%A")
    time_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    weekday = now_utc.weekday()
    hour = now_utc.hour

    is_forex_closed = (
        weekday == 5
        or (weekday == 6 and hour < 21)
        or (weekday == 4 and hour >= 21)
    )

    if is_forex_closed:
        market_status = (
            "WEEKEND STATUS: Forex pairs (EUR/USD, GBP/USD, USD/JPY, etc.) and "
            "Commodities (Gold/XAUUSD) are CURRENTLY CLOSED until Sunday 21:00 UTC (5 PM EST). "
            "Only 24/7 Crypto markets (BTC/USD, ETH/USD, SOL/USD) are open and active. "
            "Never suggest live trades on Forex/Gold during weekends."
        )
    else:
        sessions = []
        if 7 <= hour < 16:
            sessions.append("London Session")
        if 12 <= hour < 21:
            sessions.append("New York Session")
        if hour >= 21 or hour < 7:
            sessions.append("Asian / Sydney Session")
        sess_name = " & ".join(sessions) if sessions else "Inter-session"
        market_status = f"LIVE SESSION: Forex, Gold, and Crypto are ALL OPEN. Active session: {sess_name}."

    acct_block = ""
    pos_block = ""
    rec_block = ""

    if bot_status:
        acc_label = bot_status.get("account", "Active")
        bal = bot_status.get("balance", 0.0)
        eq = bot_status.get("equity", bal)
        margin_free = bot_status.get("free_margin", bal)
        acct_info = bot_status.get("account_info") or {}
        server = acct_info.get("server", "Exness")
        login = acct_info.get("login", "?")
        leverage = acct_info.get("leverage", 2000)

        acct_block = (
            "[LIVE MT5 ACCOUNT DETAILS]\n"
            f"• Profile: {acc_label} (Login: {login} on {server})\n"
            f"• Balance: ${bal:.2f} USD | Equity: ${eq:.2f} USD | Free Margin: ${margin_free:.2f} USD\n"
            f"• Leverage: 1:{leverage}\n"
        )

        positions = bot_status.get("positions", [])
        if positions:
            pos_lines = []
            for p in positions:
                side = "BUY" if p.get("type", 0) == 0 else "SELL"
                sym = p.get("symbol", "?")
                vol = p.get("volume", 0.0)
                price_open = p.get("price_open", 0.0)
                pnl = p.get("profit", 0.0)
                pnl_disp = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                ticket = p.get("ticket", "?")
                pos_lines.append(f"  • #{ticket} | {sym} {side} {vol} lots @ {price_open} | Floating P&L: {pnl_disp}")
            pos_block = "[CURRENT OPEN POSITIONS]\n" + "\n".join(pos_lines) + "\n"
        else:
            pos_block = "[CURRENT OPEN POSITIONS]\n• No open trades active right now.\n"

        recent_trades = bot_status.get("recent_trades", [])
        if recent_trades:
            rec_lines = []
            for r in recent_trades[:3]:
                pnl = r.get("result_usd", 0.0)
                pnl_disp = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                rec_lines.append(f"  • {r.get('symbol')} ({r.get('direction', '').upper()}): {pnl_disp} via {r.get('trigger', '')}")
            rec_block = "[RECENT CLOSED TRADES]\n" + "\n".join(rec_lines) + "\n"

    retcodes_ref = (
        "[MT5 RETURN CODE REFERENCE]\n"
        "• 10014: TRADE_RETCODE_INVALID_VOLUME (Invalid volume / lot size / broker step mismatch)\n"
        "• 10015: TRADE_RETCODE_INVALID_PRICE (Invalid order price)\n"
        "• 10016: TRADE_RETCODE_INVALID_STOPS (Invalid Stop Loss or Take Profit distance)\n"
        "• 10018: TRADE_RETCODE_MARKET_CLOSED (Market is closed for trading)\n"
        "• 10019: TRADE_RETCODE_NO_MONEY (Not enough margin to open position)\n"
    )

    return (
        f"[LIVE TIMING & MARKET ENVIRONMENT]\n"
        f"• Current UTC Time: {time_str} ({day_name})\n"
        f"• Market Condition: {market_status}\n"
        f"{acct_block}\n"
        f"{pos_block}\n"
        f"{rec_block}\n"
        f"{retcodes_ref}"
    )


# ── Core AI Calling Functions (100% Google Gemini) ──────────────────

def call_gemini(prompt: str = "", system_instruction: str = "", history: list[dict] = None, json_mode: bool = False) -> str:
    """
    Calls Google Gemini API using an active multi-key pool with automatic failover across keys and models.
    Cycles through all configured API keys if one encounters rate limits (429) or service issues (503).
    """
    keys = getattr(config, "GEMINI_API_KEYS", [])
    if not keys:
        single_key = getattr(config, "GEMINI_API_KEY", "").strip()
        if single_key:
            keys = [single_key]
    if not keys:
        raise RuntimeError("No Gemini API keys configured in .env or config.")

    models_to_try = [
        getattr(config, "GEMINI_MODEL", "gemini-2.5-flash"),
        "gemini-3.8-flash",
        "gemini-flash-latest"
    ]
    seen = set()
    models = [m for m in models_to_try if not (m in seen or seen.add(m))]

    contents = []
    if history:
        for turn in history:
            role = "user" if turn.get("role") == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": turn.get("text", "")}]
            })
    if prompt:
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })
    if not contents:
        raise ValueError("Either prompt or history must be provided.")

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        }
    }
    if json_mode:
        # Gemini 2.5 "thinking" tokens count against maxOutputTokens and were truncating the
        # JSON mid-string ("Unterminated string"), which the gatekeeper treated as a rejection.
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    errors = []
    for k_idx, key in enumerate(keys):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                r = requests.post(url, json=payload, timeout=TIMEOUT)
                if r.status_code == 200:
                    data = r.json()
                    cand = data.get("candidates", [])
                    if cand and "content" in cand[0] and "parts" in cand[0]["content"]:
                        return cand[0]["content"]["parts"][0]["text"].strip()
                elif r.status_code in (429, 503):
                    logger.warning(f"Gemini Key #{k_idx+1} ({model}) HTTP {r.status_code}. Cycling key...")
                    break
                elif r.status_code == 404:
                    continue  # Model not supported on this specific key, try next model in pool
                else:
                    errors.append(f"Key #{k_idx+1} {model}: HTTP {r.status_code}")
                    break
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(f"Gemini Key #{k_idx+1} ({model}) timeout ({e}). Cycling key...")
                break

    raise RuntimeError(f"All {len(keys)} Gemini keys exhausted. Errors: {'; '.join(errors[-3:])}")


def query_ai(prompt: str = "", system_instruction: str = "", history: list[dict] = None, json_mode: bool = False) -> tuple[str, str]:
    """Direct Gemini AI query. Returns (response_text, provider_name)."""
    try:
        res = call_gemini(prompt, system_instruction, history=history, json_mode=json_mode)
        return res, "Google Gemini 2.5 Flash"
    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        raise e


# ── Specialized Trading Functions ─────────────────────────────────

SYSTEM_QUANT_PROMPT = """You are an elite quantitative institutional FX & Crypto trader and master of the FXAlexG & Smart Money Concepts (SMC) strategy.
You have FULL DECISION AUTHORITY over trade execution.
You evaluate setups strictly based on:
1. Multi-Timeframe Trend Alignment (Weekly, Daily, 4H)
2. Dealing Range Equilibrium & Optimal Trade Entry (OTE 61.8% - 79% Fibonacci discount for longs, premium for shorts)
3. Liquidity Sweeps, Change of Character (CHoCH), Order Blocks / Areas of Interest (AOI), and Fair Value Gaps (FVG)
4. Traps: Liquidity grabs against the retail crowd, low ADX consolidation chop, upcoming high-impact economic news
5. Risk-to-Reward ratio (minimum 1:2.0 RR)
6. Episodic Memory Rules: Strictly enforce self-learned lessons from prior wins and losses on this symbol.

Be decisive, analytical, and uncompromising in protecting trading capital."""


def evaluate_setup(setup: dict, market_context: dict = None) -> dict:
    """
    AI Pre-Trade Gatekeeper with FULL DECISION CONTROL.
    Evaluates a candidate trade setup before execution.
    Recalls relevant past trade lessons for this symbol & trigger from episodic memory.
    FAIL-SAFE: Never approve blindly if AI call fails - rejects trade to preserve capital.
    """
    sym = setup.get("symbol", "UNKNOWN")
    direction = setup.get("direction", "UNKNOWN").upper()
    entry = setup.get("entry")
    sl = setup.get("sl")
    tp1 = setup.get("tp1")
    tp2 = setup.get("tp2")
    rr = setup.get("rr", "N/A")
    grade = setup.get("grade", "N/A")
    score = setup.get("score", 0)
    confluences = ", ".join(setup.get("met", []))
    adx = setup.get("adx", "N/A")
    trigger = setup.get("trigger", "N/A")

    policy_str = ""
    try:
        import ai_trainer
        pol = ai_trainer.load_agent_policy()
        axioms_text = "\n".join(f"• {a}" for a in pol.get("axioms", []))
        policy_str = f"""
Active Evolved Agent Policy (Generation {pol.get('generation', 1)}: {pol.get('name', 'Adaptive SMC')}):
{axioms_text}
(MANDATORY: Reject any setup that violates these evolved axioms.)
"""
    except Exception as pe:
        logger.debug(f"Policy injection skipped: {pe}")

    memory_str = ""
    try:
        import trade_logger
        past_memories = trade_logger.get_relevant_memories(sym, trigger, limit=3)
        if past_memories:
            mem_lines = []
            for m in past_memories:
                res_type = "WIN" if m.get("outcome") == "WIN" else "LOSS"
                pnl_val = m.get("pnl", 0.0)
                pnl_disp = f"+${pnl_val:.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):.2f}"
                mem_lines.append(f"• Past {res_type} ({pnl_disp}): {m.get('lesson')}")
            memory_str = (
                f"\nSelf-Learned Past Lessons on {sym}:\n"
                + "\n".join(mem_lines)
                + "\n(CRITICAL: Verify whether this candidate setup violates any past lessons or repeats past mistakes.)\n"
            )
    except Exception as e:
        logger.debug(f"Memory recall skipped: {e}")

    ctx_str = ""
    if market_context:
        ctx_str = (
            f"\nAdditional Market Context:\n"
            f"- Account Balance: ${market_context.get('balance', 'N/A')}\n"
            f"- ATR: {market_context.get('atr', 'N/A')}\n"
            f"- 4H RSI: {market_context.get('rsi', 'N/A')}\n"
            f"- Distance to nearest AOI: {market_context.get('aoi_dist', 'At AOI')}\n"
            f"- Upcoming Economic News: {market_context.get('news', 'None reported')}\n"
            f"- Spread Points: {market_context.get('spread', 'Normal')}\n"
        )

    prompt = f"""Evaluate this candidate trade setup:

Symbol: {sym}
Direction: {direction}
Entry Price: {entry}
Stop Loss: {sl}
Take Profit 1: {tp1}
Take Profit 2: {tp2}
Risk/Reward Ratio: 1:{rr}
Mechanical Grade: {grade} ({score} points)
Trigger: {trigger}
4H ADX Trend Strength: {adx}
Confluences Detected: {confluences}
{policy_str}
{memory_str}
{ctx_str}

Analyze this setup and output ONLY a valid JSON object with these exact keys:
{{
  "verdict": "APPROVED" | "REJECTED" | "CAUTION",
  "confidence": <integer 0 to 100>,
  "risk_quality": "HIGH" | "MEDIUM" | "POOR",
  "trap_detected": <true or false>,
  "trap_warning": "<description of any trap or false breakout risk, or 'None'>",
  "violates_past_memory": <true or false>,
  "memory_check": "<if violates_past_memory, explain which learned rule was breached; otherwise 'Consistent with past rules'>",
  "narrative": "<2-sentence crisp institutional breakdown of why this setup should or should not be taken>"
}}
Do NOT wrap in markdown code fences. Return raw JSON only."""

    try:
        raw, provider = query_ai(prompt, SYSTEM_QUANT_PROMPT, json_mode=True)
        clean = re.sub(r"^```json\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"```$", "", clean.strip(), flags=re.MULTILINE)
        result = json.loads(clean)
        result["provider"] = provider

        if result.get("violates_past_memory") is True:
            logger.warning(f"Trade on {sym} flagged for violating past memory: {result.get('memory_check')}")
            if result.get("verdict") == "APPROVED":
                result["verdict"] = "REJECTED"
                result["confidence"] = min(result.get("confidence", 50), 45)
                result["narrative"] = f"[Memory Veto] Setup rejected because it repeats a past loss pattern: {result.get('memory_check')}."

        return result
    except Exception as e:
        logger.error(f"Setup evaluation failed: {e}")
        return {
            "verdict": "REJECTED",
            "confidence": 0,
            "risk_quality": "POOR",
            "trap_detected": True,
            "trap_warning": f"AI Gatekeeper temporarily unreachable ({e}). Capital preserved by Fail-Safe.",
            "violates_past_memory": False,
            "memory_check": "Evaluation bypassed due to API timeout.",
            "narrative": "System Fail-Safe: Order blocked to protect capital because AI evaluation was unavailable. Never risk money without verified analysis.",
            "provider": "Safety Fail-Safe"
        }


def analyze_market_pair(symbol: str, tf_data: dict) -> str:
    """On-Demand Deep Pair Analysis for Telegram symbol query."""
    prompt = f"""Provide an institutional-grade technical and price action analysis for {symbol}.

Current Market Structure Data:
- Current Price: {tf_data.get('price')}
- Weekly Bias: {tf_data.get('w_bias')}
- Daily Trend: {tf_data.get('d_trend')} ({tf_data.get('d_ema200_side')})
- 4H Bias: {tf_data.get('h4_bias')} (EMA21: {tf_data.get('h4_ema21')})
- 4H ADX Trend Strength: {tf_data.get('adx')}
- 4H RSI: {tf_data.get('rsi')}
- Dealing Range: Low {tf_data.get('swing_low')} | High {tf_data.get('swing_high')} | Equilibrium {tf_data.get('equilibrium')} ({tf_data.get('zone_position')})
- Nearest Key Area of Interest: {tf_data.get('nearest_aoi')}
- Spread Points: {tf_data.get('spread')}

Format your response clearly with HTML tags for Telegram:
<b>{symbol} — Institutional Market Breakdown</b>
• <b>Bias & Structure</b>: (W1/D1/4H summary)
• <b>Key Levels</b>: (Support/Resistance/AOI/Equilibrium)
• <b>Trade Scenarios</b>: (Bullish trigger vs Bearish trigger)
• <b>Risk & Trap Warning</b>: (Chop/news/invalidation levels)
• <b>Actionable Stance</b>: (LONG / SHORT / WAIT)"""

    try:
        raw, provider = query_ai(prompt, SYSTEM_QUANT_PROMPT)
        return raw + f"\n\n<i>Powered by {provider}</i>"
    except Exception as e:
        return f"❌ AI Analysis error for {symbol}: {str(e)}"


def generate_session_pulse(symbols_summary: list) -> str:
    """Generates macro session commentary across scanned pairs."""
    summary_text = "\n".join(symbols_summary)
    prompt = f"""Review the current state of tracked Forex, Crypto, and Gold pairs:
{summary_text}

Provide a concise 3-4 sentence Macro Pulse for the trading session:
1. US Dollar & Risk Sentiment (Risk-On vs Risk-Off)
2. Best setup developing right now
3. Key pitfall to avoid today.

Format with bold HTML tags suitable for Telegram."""

    try:
        raw, provider = query_ai(prompt, SYSTEM_QUANT_PROMPT)
        return raw + f"\n\n<i>🧠 Market Pulse by {provider}</i>"
    except Exception as e:
        return f"Market Pulse unavailable: {e}"


def review_trade_outcome(trade: dict) -> str:
    """
    Post-trade review after trade hits TP or SL.
    Conducts autopsy, extracts an actionable self-learning rule, and logs it to trade_memories.
    """
    sym = trade.get("symbol", "UNKNOWN")
    direction = trade.get("direction", "UNKNOWN")
    pnl = trade.get("profit", 0.0)
    outcome = "WIN" if pnl > 0 else "LOSS"
    ticket = trade.get("ticket", 0)
    trigger = trade.get("trigger", "structure_break")

    prompt = f"""Conduct a post-trade review and extract one permanent self-learning rule:
Symbol: {sym}
Direction: {direction}
Trigger Pattern: {trigger}
Outcome: {outcome} (${pnl:+.2f})
Entry: {trade.get('entry')} | Exit: {trade.get('exit_price', 'Market')}
Planned SL: {trade.get('sl')} | Planned TP: {trade.get('tp')}

Output ONLY valid JSON with these exact keys:
{{
  "review": "<2-sentence clear institutional explanation of why the trade won or lost>",
  "lesson": "<One strict, actionable rule under 20 words for future trades on this symbol to prevent repeating loss or repeat win>"
}}
Do NOT wrap in markdown fences. Raw JSON only."""

    try:
        raw, provider = query_ai(prompt, SYSTEM_QUANT_PROMPT, json_mode=True)
        clean = re.sub(r"^```json\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"```$", "", clean.strip(), flags=re.MULTILINE)
        data = json.loads(clean)
        review_text = data.get("review", "").strip()
        lesson_text = data.get("lesson", "").strip()

        if lesson_text:
            try:
                import trade_logger
                trade_logger.log_trade_memory(ticket, sym, direction, trigger, outcome, pnl, lesson_text)
                logger.info(f"Learned rule recorded for {sym}: {lesson_text}")
            except Exception as le:
                logger.warning(f"Failed to record trade memory: {le}")

        return f"{review_text}\n\n🧠 <b>Self-Learned Rule:</b>\n<i>\"{lesson_text}\"</i>"
    except Exception as e:
        logger.warning(f"Structured post-mortem fallback: {e}")
        fallback_lesson = f"On {sym} {direction.upper()}, verify HTF trend and wait for 15M structure shift before execution."
        try:
            import trade_logger
            trade_logger.log_trade_memory(ticket, sym, direction, trigger, outcome, pnl, fallback_lesson)
        except Exception:
            pass
        return f"Trade closed with {outcome} of ${pnl:.2f}.\n\n🧠 <b>Self-Learned Rule:</b>\n<i>\"{fallback_lesson}\"</i>"


# ── Conversational AI Trading Desk ────────────────────────────────

SYSTEM_CHAT_PROMPT = """You are an experienced, elite institutional trader and the user's personal trading partner inside this bot.
You talk directly to the user on Telegram.
You have FULL ACCESS to the bot's live MT5 account, actual open positions, balance, equity, and trade history.

CRITICAL INSTRUCTIONS:
1. COMPLETE TRUTHFULNESS ON OPEN TRADES:
   - Check the [CURRENT OPEN POSITIONS] section provided in the market context!
   - If there are open positions, state the EXACT symbol, direction, lot size, entry price, and current profit.
   - If there are NO open positions, explicitly state that the bot is flat and actively scanning for setups.
   - NEVER make up or guess positions (e.g., never say we are in EUR/USD if the position list shows ETH or is empty).
2. MT5 ERROR CODES ACCURACY:
   - If the user asks about an error code like 10014, explain accurately:
     10014 = TRADE_RETCODE_INVALID_VOLUME (Invalid volume or lot size step mismatch).
     10019 = TRADE_RETCODE_NO_MONEY (Insufficient margin to open trade).
     10018 = TRADE_RETCODE_MARKET_CLOSED (Weekend or session closed).
3. MARKET HOURS ACCURACY:
   - On weekends (Friday 21:00 UTC to Sunday 21:00 UTC), Forex and Gold are strictly CLOSED.
   - Only Crypto (Bitcoin, Ethereum, Solana) trades 24/7.
4. TONE & STYLE:
   - Speak in warm, natural, human language — like a smart, friendly, honest trading partner who is genuinely in their corner.
   - Be direct, confident, and insightful.
   - Keep responses clean and easy to read using Telegram HTML (<b>bold</b> for key levels/prices, <i>italics</i> for side thoughts).
   - Never output raw JSON or code blocks unless requested.
   - Do NOT dump boilerplate disclaimers."""


def chat_ai(user_query: str, bot_status: dict = None) -> str:
    """
    Interactive AI assistant for Telegram conversational chat.
    Uses multi-turn persistent history and complete live MT5 market context.
    """
    market_env = get_market_context_str(bot_status)
    system_instruction = f"{SYSTEM_CHAT_PROMPT}\n\n{market_env}"
    history = load_chat_history(max_turns=10)

    try:
        raw, provider = query_ai(prompt=user_query, system_instruction=system_instruction, history=history)

        save_chat_turn("user", user_query)
        save_chat_turn("model", raw)

        return f"{raw}\n\n<i>🤖 Powered by {provider}</i>"
    except Exception as e:
        logger.warning(f"Gemini query failed ({e}), using Built-in Emergency Response")
        save_chat_turn("user", user_query)

        bal = bot_status.get('balance', 20.0) if bot_status else 20.0
        open_positions = bot_status.get('positions', []) if bot_status else []
        pos_summary = f"{len(open_positions)} open position(s)" if open_positions else "no open trades"

        emergency_ans = (
            f"👋 <b>Hey trader! Live Desk Quick Status:</b>\n\n"
            f"• <b>Balance:</b> ${bal:.2f} USD\n"
            f"• <b>Status:</b> Currently holding {pos_summary}.\n"
            f"• <b>Notice:</b> AI Brain is temporarily reconnecting ({str(e)[:60]}). Orders remain protected by MT5 hard stops."
        )
        save_chat_turn("model", emergency_ans)
        return emergency_ans