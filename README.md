# FXAlexG Trading Bot — Complete Code Architecture

> **Version:** v3.3 (AI-Powered Institutional Target Mode)
> **Language:** Python 3
> **Broker:** Exness (via MetaTrader 5)
> **AI Providers:** Google Gemini 2.5 Flash (primary) + OpenRouter DeepSeek (fallback)
> **Interface:** Telegram Bot with 1-Tap Interactive Keyboard

---

## Table of Contents

1. [High-Level System Overview](#1-high-level-system-overview)
2. [System Architecture Diagram](#2-system-architecture-diagram)
3. [Module-by-Module Breakdown](#3-module-by-module-breakdown)
   - [config.py — Configuration & Parameters](#31-configpy--configuration--parameters)
   - [bot.py — Main Orchestrator](#32-botpy--main-orchestrator)
   - [strategy.py — ICT Strategy Engine](#33-strategypy--ict-strategy-engine)
   - [ai_engine.py — Dual-AI Intelligence Engine](#34-ai_enginepy--dual-ai-intelligence-engine)
   - [risk_manager.py — Position Sizing & Risk Controls](#35-risk_managerpy--position-sizing--risk-controls)
   - [mt5_bridge.py — MetaTrader 5 HTTP Bridge Server](#36-mt5_bridgepy--metatrader-5-http-bridge-server)
   - [bridge_client.py — HTTP Client for MT5 Bridge](#37-bridge_clientpy--http-client-for-mt5-bridge)
   - [data_provider.py — Market Data Provider](#38-data_providerpy--market-data-provider)
   - [notifier.py — Telegram Notification System](#39-notifierpy--telegram-notification-system)
   - [trade_logger.py — SQLite Trade Database](#310-trade_loggerpy--sqlite-trade-database)
   - [news_filter.py — Economic News Filter](#311-news_filterpy--economic-news-filter)
   - [weekly_analyst.py — Weekend Top-Down Analysis](#312-weekly_analystpy--weekend-top-down-analysis)
   - [analytics.py — Historical Trade Analytics](#313-analyticspy--historical-trade-analytics)
   - [backtester.py — Multi-Timeframe Backtester](#314-backtesterpy--multi-timeframe-backtester)
4. [Data Flow: From Scan to Trade Execution](#4-data-flow-from-scan-to-trade-execution)
5. [Trade Lifecycle Management](#5-trade-lifecycle-management)
6. [Telegram Command System](#6-telegram-command-system)
7. [Main Event Loop Timing](#7-main-event-loop-timing)
8. [Grading & Quality Gate System](#8-grading--quality-gate-system)
9. [Risk Management Architecture](#9-risk-management-architecture)
10. [AI Gatekeeper Pipeline](#10-ai-gatekeeper-pipeline)
11. [File Inventory](#11-file-inventory)
12. [Utility & Debug Scripts](#12-utility--debug-scripts)
13. [Configuration Data Files](#13-configuration-data-files)

---

## 1. High-Level System Overview

The FXAlexG Bot is a **fully automated institutional-grade trading bot** that:

1. **Scans 13 Forex, Crypto & Gold pairs** every 4 hours (on 4H candle closes) and every 15 minutes during ICT Kill Zones (London, New York, Asian sessions).
2. **Applies a multi-timeframe ICT/Smart Money Concepts (SMC) strategy** across Weekly → Daily → 4H → 15M timeframes.
3. **Grades each setup** on a 100-point scoring system (F → C+ → B+ → A+). Only A+ setups (≥95 points) are tradeable; B+ setups (≥78 points, score ≥80) can be upgraded by AI.
4. **Passes every candidate trade through a Dual-AI Gatekeeper** (Google Gemini 2.5 Flash + OpenRouter DeepSeek) for trap detection, liquidity grab analysis, and institutional confirmation.
5. **Executes orders on MetaTrader 5** via a local HTTP bridge server running on the same machine.
6. **Manages open positions** with partial take-profit at TP1 (1:1.0 RR), breakeven SL move, 15M EMA21 trailing stop, trade TTL (36h max lifespan), and 4H EMA invalidation early exits.
7. **Communicates entirely through Telegram** with an interactive tap-button keyboard, AI-powered chat, and real-time notifications.

The system is split into **two independent processes**:
- **`mt5_bridge.py`** — A standalone HTTP server that wraps the MetaTrader 5 Python API. Runs on the machine with MT5 installed.
- **`bot.py`** — The main trading bot. Communicates with the bridge over HTTP. Can run on any machine that can reach the bridge URL.

---

## 2. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FXAlexG Trading Bot                          │
│                                                                      │
│  ┌─────────┐    ┌───────────┐    ┌────────────┐    ┌──────────────┐ │
│  │ config  │───▶│  bot.py   │───▶│ strategy   │───▶│ ai_engine    │ │
│  │  .py    │    │ (main     │    │   .py      │    │   .py        │ │
│  │         │    │  loop)    │    │ (ICT/SMC)  │    │ (Gemini +    │ │
│  └─────────┘    └─────┬─────┘    └────────────┘    │  OpenRouter) │ │
│                       │                             └──────────────┘ │
│              ┌────────┼────────┬─────────────┐                       │
│              ▼        ▼        ▼             ▼                       │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐             │
│  │ bridge_   │ │ notifier │ │ trade_   │ │ news_     │             │
│  │ client.py │ │   .py    │ │ logger   │ │ filter.py │             │
│  │ (HTTP)    │ │(Telegram)│ │   .py    │ │(Forex     │             │
│  └─────┬─────┘ └──────────┘ │ (SQLite) │ │ Factory)  │             │
│        │                     └──────────┘ └───────────┘             │
│        │HTTP                                                         │
│        ▼                                                             │
│  ┌────────────┐    ┌──────────────┐                                  │
│  │ mt5_bridge │───▶│  MetaTrader  │                                  │
│  │   .py      │    │  5 Terminal  │                                  │
│  │ (HTTP Srv) │    │  (Exness)    │                                  │
│  └────────────┘    └──────────────┘                                  │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ data_        │  │ risk_        │  │ weekly_      │               │
│  │ provider.py  │  │ manager.py   │  │ analyst.py   │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘

External Services:
  ├── Google Gemini 2.5 Flash API (generativelanguage.googleapis.com)
  ├── OpenRouter API (openrouter.ai) → DeepSeek Chat
  ├── Telegram Bot API (api.telegram.org)
  ├── ForexFactory Calendar JSON (nfs.faireconomy.media)
  └── Exness MT5 Broker (via MetaTrader 5 terminal)
```

---

## 3. Module-by-Module Breakdown

### 3.1 `config.py` — Configuration & Parameters

**Purpose:** Central configuration file. Every tunable parameter in the system lives here.

| Section | Key Parameters | Description |
|---------|---------------|-------------|
| **Telegram** | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Bot token and auto-discovered chat ID |
| **MT5 Bridge** | `BRIDGE_URL` | HTTP URL to mt5_bridge.py (default `localhost:5001`) |
| **Account Profiles** | `ACCOUNTS` dict with `demo` and `real` keys | Login, password, server for each profile. `ACTIVE_ACCOUNT` selects which is active |
| **Symbols** | `SYMBOLS` list of 13 pairs | `EURUSDm`, `GBPUSDm`, `USDJPYm`, `AUDUSDm`, `USDCADm`, `EURJPYm`, `GBPJPYm`, `BTCUSDm`, `ETHUSDm`, `XAUUSDm`, `SOLUSDm`, `XRPUSDm`, `LTCUSDm` |
| **Risk Engine** | `RISK_PERCENT=7.5`, `MIN_RISK_USD=1.50`, `MAX_RISK_USD=15.0`, `MAX_RISK_PCT=10.0` | Targets $3.50-$5.00 risk per trade for $10+ payouts |
| **Trade Limits** | `MAX_TRADES_DAY=4`, `MAX_TRADES_KILL_ZONE=2`, `MAX_CONCURRENT=2` | Caps daily, per-session, and concurrent open trades |
| **TP/SL/Trailing** | `MIN_RR=2.0`, `TP1_RR=1.0`, `TP2_RR=3.0`, `PARTIAL_PCT=0.5`, `USE_TRAILING_STOP=True`, `TRAILING_STOP_BUFFER=5` | Dual TP with 50% partial close, trailing after TP1 |
| **Quality Filters** | `ADX_MIN=20`, `COOLDOWN_HOURS=24`, `MIN_GRADE="A+"`, `MIN_TF_SYNC=2` | ADX trending filter, post-loss cooldown, grade threshold |
| **AI Engine** | `AI_ENABLED=True`, `AI_MIN_CONFIDENCE=70`, `AI_UPGRADE_B_PLUS=True` | Dual-provider gatekeeper with B+ upgrade path |
| **Spreads** | `NORMAL_SPREAD_POINTS` dict, `MAX_SPREAD_MULT=3` | Per-symbol spread baselines; skip if live spread > 3× baseline |
| **Strategy Params** | `AOI_TOLERANCE`, `EMA_FAST/SLOW/TREND`, `RSI_*` bounds, `SCAN_HOURS` | EMAs (9/21/200), RSI bounds (32-68), scan at 4H closes |
| **Trade TTL** | `MAX_TRADE_LIFESPAN_HOURS=36` | Force-close if TP1 not hit in 36h |
| **SL Lookback** | `CRYPTO_SL_LOOKBACK=12`, `FOREX_SL_LOOKBACK=6` | Deeper swing structure for volatile crypto |

---

### 3.2 `bot.py` — Main Orchestrator

**Purpose:** The central brain. Contains the main event loop, scan orchestration, position management, and Telegram command handler.

**Lines:** 827 | **Imports:** `config`, `bridge_client`, `data_provider`, `strategy`, `risk_manager`, `trade_logger`, `news_filter`, `notifier`, `ai_engine`, `weekly_analyst`

#### Key Functions

| Function | Lines | Description |
|----------|-------|-------------|
| `load_active_account()` | 37-44 | Reads `active_account.json` to determine demo/real profile |
| `save_active_account(acc_key)` | 47-51 | Persists account selection to JSON |
| `current_kill_zone()` | 58-73 | Returns `"london"` (07-10 UTC), `"new_york"` (12-15 UTC), `"asian"` (00-04 UTC), or `None` |
| `check_closed_positions()` | 76-104 | Polls `trade_logger` for open trade tickets, cross-references with live MT5 positions. If a ticket is no longer live, fetches realized P&L from bridge history, logs closure, sends Telegram notification. Optionally triggers **AI post-trade review** |
| `manage_open_positions()` | 107-222 | **The position management engine.** For each open trade: (1) Checks **TTL** — force-closes if open > 36h without TP1 hit. (2) Checks **TP1 hit** — does partial close (50%), moves SL to breakeven + spread-aware buffer, extends TP to TP2. (3) **Trailing stop** — after TP1, trails SL behind 15M EMA21 with pip buffer |
| `check_ema_invalidation()` | 225-259 | Every 15 min, checks if the **last closed 4H candle** has crossed below/above EMA21 against the trade direction. If invalidated, closes the position early |
| `run_scan(kill_zone)` | 262-456 | **The main scan engine.** For each of the 13 symbols: (1) Checks balance > minimum. (2) Checks daily/KZ trade limits. (3) Checks concurrent position limit. (4) Checks post-loss cooldown. (5) Checks high-impact news. (6) Checks spread. (7) Fetches W1/D1/4H/15M data. (8) Calls `strategy.analyze_pair()`. (9) Grade gate: only A+ or AI-upgradeable B+. (10) Deduplicates setups. (11) **AI Gatekeeper**: queries dual-AI for verdict, confidence, trap detection. (12) Calculates lot size via `risk_manager`. (13) Places order via `bridge_client`. (14) Logs trade and sends Telegram notification |
| `handle_telegram_commands()` | 459-502 | Polls Telegram for new messages. Matches button taps and natural language to commands: Market Pulse, Balance, Gold/EUR/BTC analysis, P&L Report, News, Switch Account, Help/Menu. Unrecognized text → freeform AI chat |
| `_handle_ai_command(item)` | 505-625 | Routes `/ai` sub-commands: `status` (ping dual-AI), `pulse` (macro session commentary), `[symbol]` (deep SMC analysis with full multi-TF data), or freeform query (interactive AI chat with bot status context) |
| `_switch_account(target)` | 628-668 | Switches between demo/real. Blocks if positions are open. Calls `bridge.switch_account()`, updates `active_account.json` |
| `_send_report()` | 672-691 | Fetches today's and this week's P&L from bridge `/report/` endpoint |
| `_show_account_info()` | 694-713 | Shows account balance, equity, leverage, open positions |
| `main()` | 716-826 | **Entry point.** Initializes DB, loads active account, waits for Telegram `/start`, connects to MT5 bridge, enters infinite loop. The loop: (1) Checks closed positions every cycle. (2) Polls Telegram every 5s. (3) Manages open positions every 15s. (4) Checks 4H EMA invalidation every 15m. (5) Runs weekend analysis on Saturday. (6) Auto-broadcasts daily news at 06:00 UTC. (7) Triggers scans at 4H candle closes or every 15m during Kill Zones. Sleeps 15s between iterations |

#### Deduplication Tracker
```python
_last_signaled = {}  # {symbol: (direction, swing_eq)}
```
Prevents the bot from repeatedly signaling the exact same setup (same direction + same swing equilibrium level).

---

### 3.3 `strategy.py` — ICT Strategy Engine

**Purpose:** Implements the full ICT/Smart Money Concepts strategy. All technical analysis, pattern detection, grading, and SL/TP calculation lives here.

**Lines:** 693 | **Imports:** `pandas`, `numpy`, all config constants

#### Indicator Functions

| Function | Description |
|----------|-------------|
| `_ema(series, length)` | Exponential Moving Average using pandas `ewm()` |
| `_rsi(series, length=14)` | Wilder's RSI (14-period) |
| `_atr(high, low, close, length=14)` | Average True Range (14-period, Wilder smoothed) |
| `_adx(high, low, close, length=14)` | Average Directional Index (Wilder's full implementation with +DI, -DI, DX smoothing) |
| `add_indicators(df)` | Adds all 6 indicator columns to a DataFrame: `ema9`, `ema21`, `ema200`, `rsi`, `atr`, `adx` |

#### Swing Range & OTE Zone

| Function | Description |
|----------|-------------|
| `get_swing_range(df, lookback=30)` | Calculates the active dealing range from the last 30 bars. Returns `high`, `low`, `equilibrium` (midpoint), `discount_ote` (low + 0.618 × range), `premium_ote` (high − 0.618 × range) |
| `is_price_in_favorable_zone(price, direction, swing_info)` | **TRUE OTE enforcement.** Longs: price must be ≤ discount OTE boundary. Shorts: price must be ≥ premium OTE boundary |
| `_is_volatile(symbol)` | Returns `True` for BTC, ETH, LTC, XRP, SOL, XAU — used for adaptive SL sizing |

#### AOI & Key Level Detection

| Function | Description |
|----------|-------------|
| `find_aoi_zones(df, symbol)` | Identifies multi-touch support/resistance zones by clustering swing pivots and recent closes within `AOI_TOLERANCE` (0.25%). Each zone has `low`, `high`, `mid`, `touches` count, and `has_psych` flag |
| `price_in_aoi(price, zones, buffer_pct=0.0015)` | Checks if price is currently inside any AOI zone (with 0.15% buffer) |
| `get_key_levels(df_d, df_w)` | Extracts PDH (Previous Day High), PDL, PWH, PWL from last closed candles |
| `near_key_level(price, levels, tolerance=0.0025)` | Returns `True` if price is within 0.25% of any PDH/PDL/PWH/PWL level |
| `_psych_level(price, symbol)` | Rounds to nearest psychological level (Gold: $100, BTC: $1000, ETH: $50, JPY: ¥5, Forex: 0.05) |
| `get_equilibrium(df, n=100)` | Midpoint of the last n bars' high-low range |

#### Trend Bias Functions

| Function | Description |
|----------|-------------|
| `get_weekly_bias(df_w)` | `"bullish"` if close > EMA21, `"bearish"` if close < EMA21, else `"neutral"` |
| `get_daily_trend(df_d)` | `"bullish"` if close > EMA200, `"bearish"` if close < EMA200, else `"neutral"` |
| `get_4h_bias(df_4h)` | Uses the **last closed candle** (`iloc[-2]`): `"bullish"` if close > EMA21 |
| `count_synced_timeframes(w_bias, d_trend, h4_bias, direction)` | Counts 0-3 how many timeframes agree with the proposed trade direction |
| `rsi_ok(df_4h, direction)` | Checks RSI is within bounds (32-68 for both longs and shorts) — filters extreme oversold/overbought |

#### Entry Trigger Detection

| Function | Description |
|----------|-------------|
| `check_engulfing(df, direction)` | 4H bullish/bearish engulfing pattern on the **last closed candle** (`iloc[-2]` vs `iloc[-3]`). Bullish: curr close > prev high. Bearish: curr close < prev low |
| `check_rejection_candle(df, direction)` | Pinbar/hammer detection. Bullish: lower wick ≥ 45% of candle range. Bearish: upper wick ≥ 45% |
| `check_shift_of_structure(df, direction, window=3)` | Detects Higher Highs (bullish) or Lower Lows (bearish) from the last two swing points with a 3-bar confirmation window |
| `detect_15m_sweep_and_choch(df_15m, direction, lookback=40)` | **15M Sniper Entry.** Detects liquidity sweep + Change of Character: price sweeps recent swing low/high then closes back inside. Returns entry, SL (swing extreme − 0.2×ATR), TP1, TP2 |
| `find_fvg_near_price(df_15m, direction, price, lookback=15)` | Checks for Fair Value Gaps near the current price within the last 15 bars |

#### SL/TP Calculation

| Function | Description |
|----------|-------------|
| `calculate_sl_tp_4h(df_4h, direction, entry_price, symbol)` | **Structural SL/TP.** SL: swing low/high of last N candles (20 for crypto/gold, 8 for forex) + ATR buffer (0.8× for volatile, 0.3× for forex). TP: first tries to find the nearest structural swing target (next swing high for longs, next swing low for shorts) with `_find_structural_target()`. If structural target has RR ≥ MIN_RR, uses it. Otherwise falls back to mechanical TP1_RR/TP2_RR multipliers |
| `_find_structural_target(df_4h, direction, entry_price, lookback=60)` | Scans for swing pivots beyond entry price, returns the nearest one |

#### Grading System

```python
def grade_setup(at_aoi, synced_tfs, ema21_ok, ema200_ok, sos, trigger_type, has_psych, rsi_good, near_key_lvl, in_zone):
```

| Confluence | Points |
|-----------|--------|
| At AOI zone | +15 |
| In Swing Discount/Premium | +15 |
| 3TF Sync (W1+D1+4H) | +18 |
| 2TF Sync | +12 |
| 4H EMA21 alignment | +10 |
| D1 EMA200 alignment | +10 |
| Shift of Structure (HH/LL) | +12 |
| 4H Engulfing trigger | +15 |
| 4H Rejection Wick trigger | +12 |
| 15M CHoCH Mitigation trigger | +15 |
| Psychological Level nearby | +5 |
| RSI Filter OK | +5 |
| PDH/PDL Key Level | +6 |
| **Maximum possible** | **~131** |

**Grade thresholds (v3.2):** A+ ≥ 95 points | B+ ≥ 78 | C+ ≥ 45 | F < 45

#### Main Analysis Entry Point

```python
def analyze_pair(symbol, df_w, df_d, df_4h, df_15m=None, in_kill_zone=False):
```

**Flow:**
1. Add indicators to all timeframes
2. **ADX Filter** — skip if 4H ADX < 20 (ranging market)
3. Calculate swing range and key levels
4. Get W1/D1/4H biases
5. For each direction (bullish, bearish):
   - **Hard Rule 1:** 4H bias must align (or be neutral)
   - **Hard Rule 2:** Price must be in TRUE OTE zone (discount for longs, premium for shorts)
   - Calculate all confluences (AOI, EMAs, SoS, triggers)
   - **PRIMARY path (Kill Zone):** If in Kill Zone + 15M data available + SoS or EMA21 OK → detect 15M sweep+CHoCH. Requires only 2TF sync. If FVG or AOI present → grade and return if A+ or AI-eligible B+
   - **SECONDARY path (4H Trigger):** If 4H engulfing or rejection + at AOI → requires 3TF sync. Calculate structural SL/TP → grade and return if A+ or AI-eligible B+
6. Returns setup dict or `None`

---

### 3.4 `ai_engine.py` — Dual-AI Intelligence Engine

**Purpose:** Wraps Google Gemini 2.5 Flash and OpenRouter (DeepSeek) APIs with automatic failover. Provides pre-trade gatekeeper, on-demand analysis, session pulse, post-trade review, and interactive chat.

**Lines:** 308 | **Timeout:** 15s per request

#### Core Functions

| Function | Description |
|----------|-------------|
| `call_gemini(prompt, system_instruction)` | Direct POST to Gemini `generateContent` endpoint. Returns raw text |
| `call_openrouter(prompt, system_instruction)` | POST to OpenRouter chat completions. Uses DeepSeek model, temp=0.2, max 1000 tokens |
| `query_ai(prompt, system_instruction)` | **Dual-provider query with automatic failover.** Tries Gemini first; if it fails, falls back to OpenRouter. Returns `(response_text, provider_name)` |

#### Specialized Trading Functions

| Function | Description |
|----------|-------------|
| `evaluate_setup(setup, market_context)` | **Pre-trade AI gatekeeper.** Sends the full setup (symbol, direction, entry, SL, TP, RR, grade, score, ADX, confluences, spread, news) to AI. Expects JSON response with: `verdict` (APPROVED/REJECTED/CAUTION), `confidence` (0-100), `risk_quality`, `trap_detected`, `trap_warning`, `narrative`. On AI failure, falls back to mechanical grade-based verdict |
| `analyze_market_pair(symbol, tf_data)` | On-demand deep pair analysis for Telegram `/ai [symbol]`. Sends W1/D1/4H bias, EMA, RSI, ADX, swing range, AOI, spread data to AI. Returns formatted Telegram HTML response |
| `generate_session_pulse(symbols_summary)` | Macro session commentary across scanned pairs. Covers USD sentiment, best developing setup, key pitfall |
| `review_trade_outcome(trade)` | Post-trade AI review after close. Identifies key technical reason for win/loss and future rule |
| `chat_ai(user_query, bot_status)` | Interactive AI assistant. Receives freeform user question + current bot status (balance, open trades, daily count). Returns conversational response |

**System prompt** (`SYSTEM_QUANT_PROMPT`): Instructs the AI to evaluate strictly based on Multi-TF alignment, OTE/Fibonacci, liquidity sweeps/CHoCH/AOI/FVG, traps, and RR ratio. Emphasizes capital protection.

---

### 3.5 `risk_manager.py` — Position Sizing & Risk Controls

**Purpose:** Calculates exact lot size so dollar risk matches the configured percentage of balance, with safety guards.

**Lines:** 123

#### Key Logic

1. **Reject** if balance < `MIN_BALANCE` ($5) or SL distance ≤ 0
2. **Calculate target risk USD:** `max(MIN_RISK_USD, balance × RISK_PERCENT / 100)`, capped at `MAX_RISK_USD`
3. **Fetch live symbol specs** from MT5 bridge (`contract_size`, `tick_value`, `tick_size`, `volume_min/max/step`)
4. **Lot calculation:**
   - If broker specs available: `lot = risk_usd / (sl_dist / tick_size × tick_value)`
   - Fallback: uses hardcoded pip values per lot from `_PIP_VALUE_PER_LOT` dict
5. **Clamp** to broker volume step, min, max
6. **Safety check:** If actual risk% exceeds `MAX_RISK_PCT` (10%), clamp lot down

**`get_pip_size(symbol)`:** Returns pip size per asset class: JPY=0.01, XAU=1.0, BTC=1.0, ETH=0.1, Forex=0.0001.

---

### 3.6 `mt5_bridge.py` — MetaTrader 5 HTTP Bridge Server

**Purpose:** Standalone HTTP server (port 5001) that wraps the `MetaTrader5` Python library. This is the **only file that directly imports `MetaTrader5`**. Must run on the machine where MT5 is installed.

**Lines:** 465

#### HTTP Endpoints

| Method | Path | Handler Function | Description |
|--------|------|-----------------|-------------|
| GET | `/health` | `get_account_dict()` | Account info: balance, equity, currency, server, login, leverage, margin_free |
| GET | `/positions` | `get_positions()` | All open positions: ticket, symbol, type, volume, open_price, current_price, sl, tp, profit |
| GET | `/symbol/{sym}` | `get_symbol_info()` | Symbol specs: digits, spread, contract_size, tick_size, tick_value, volume_min/max/step |
| GET | `/history/{ticket}` | — | Realized P&L from history deals for a closed position |
| GET | `/report/{timeframe}` | `get_pnl_report()` | P&L report for "today" or "week": profit, wins, losses, winrate, total_trades |
| GET | `/ohlcv/{sym}/{tf}/{count}` | `get_ohlcv()` | OHLCV bars. Timeframes: M1, M5, M15, M30, H1, H4, D1, W1, MN1 |
| POST | `/order` | `do_order()` | Market order execution with SL/TP. Auto-clamps volume to broker constraints. Retries with FOK filling if IOC fails |
| POST | `/close` | `close_position()` | Full or partial close of a position |
| POST | `/modify` | `modify_position()` | Modify SL and/or TP of an open position |
| POST | `/switch` | `switch_account()` | Disconnect and reconnect to a different MT5 account (demo/real). Tries direct login → falls back to full re-initialization |

#### Internal Functions

- `init_mt5()` — Initializes MT5, prints account info
- `do_order()` — Constructs MT5 order request (TRADE_ACTION_DEAL), uses IOC filling with FOK fallback
- `close_position()` — Constructs counter-trade to close; supports partial close
- `modify_position()` — TRADE_ACTION_SLTP request
- `switch_account()` — Tries `mt5.login()` directly, then `mt5.shutdown()` + `mt5.initialize()` with new credentials
- `get_pnl_report()` — Queries `mt5.history_deals_get()` for exit deals, sums profit+swap+commission

---

### 3.7 `bridge_client.py` — HTTP Client for MT5 Bridge

**Purpose:** Thin HTTP client that wraps all calls to `mt5_bridge.py`. Used by `bot.py` and other modules to interact with MT5 without importing the MetaTrader5 library directly.

**Lines:** 152 | **Timeout:** 12s (30s for account switch)

| Function | Endpoint | Description |
|----------|----------|-------------|
| `health()` | GET `/health` | Raw health response |
| `get_account()` | GET `/health` | Returns account dict if connected, else `None` |
| `get_positions()` | GET `/positions` | List of open position dicts |
| `get_symbol_info(symbol)` | GET `/symbol/{sym}` | Symbol specifications |
| `place_order(symbol, side, lot, sl, tp, comment)` | POST `/order` | Place market order. Normalizes direction strings ("bullish"→"buy") |
| `spread_ok(symbol)` | GET `/symbol/{sym}` | Checks if live spread ≤ baseline × `MAX_SPREAD_MULT` (3×) |
| `get_position_result(ticket)` | GET `/history/{ticket}` | Realized P&L for closed ticket |
| `close_position(ticket)` | POST `/close` | Full close |
| `partial_close(ticket, volume)` | POST `/close` | Close partial volume |
| `modify_sl(ticket, new_sl, new_tp)` | POST `/modify` | Modify SL (and optionally TP) |
| `switch_account(login, password, server)` | POST `/switch` | Switch MT5 account |
| `get_report(timeframe)` | GET `/report/{tf}` | P&L report ("today" or "week") |
| `is_online()` | GET `/health` | Boolean connectivity check |

---

### 3.8 `data_provider.py` — Market Data Provider

**Purpose:** Fetches OHLCV data from the MT5 bridge and returns clean pandas DataFrames.

**Lines:** 75

| Function | Timeframe | Default Count |
|----------|-----------|---------------|
| `get_weekly(symbol, count=100)` | W1 | 100 |
| `get_daily(symbol, count=200)` | D1 | 200 |
| `get_4h(symbol, count=200)` | H4 | 200 |
| `get_15m(symbol, count=200)` | M15 | 200 |
| `get_current_price(symbol)` | M30 (1 bar) | 1 |

**`_fetch(symbol, timeframe, count)`** — Core fetcher:
1. Calls bridge `/ohlcv/{sym}/{tf}/{count}`
2. Parses JSON `bars` array
3. Converts `time` to UTC datetime index
4. Returns DataFrame with columns: `open`, `high`, `low`, `close`, `volume`
5. Stores `symbol` and `timeframe` in `df.attrs`

Has a `SYMBOL_MAP` dict for Exness symbol name translation (passthrough for unmapped symbols).

---

### 3.9 `notifier.py` — Telegram Notification System

**Purpose:** All Telegram communication: sending messages, polling for commands, and the interactive keyboard.

**Lines:** 270

#### Key Components

**Persistent Keyboard** — Always-visible button grid at the bottom of Telegram:
```
[ 📊 Market Pulse ] [ 🏦 Balance & Status ]
[ 🥇 Gold ] [ 💶 EUR/USD ] [ ₿ Bitcoin ]
[ 📈 P&L Report ] [ 📰 Today's News ] [ 🔄 Switch Account ]
```

**Chat ID Discovery** — On first run, polls Telegram `getUpdates` until a user sends `/start`. Saves chat ID to `chat_id.json`.

**Offset-Based Polling** — Tracks the last processed `update_id` in `tg_offset.json` to avoid processing the same message twice.

| Function | Description |
|----------|-------------|
| `discover_chat_id()` | One-time discovery of chat ID from Telegram updates |
| `get_commands()` | Polls `getUpdates` with offset. Returns list of `{command, text}` dicts. Filters to only process messages from the bot's chat |
| `send(text, keyboard=True)` | Sends HTML-formatted message to Telegram. Attaches the persistent keyboard by default |
| `send_trade_opened(setup, lot, risk_usd, risk_pct)` | Formatted trade opened notification with all details |
| `send_trade_closed(symbol, direction, result_usd, balance, week_count)` | Win/loss notification |
| `send_scan_summary(pairs_scanned, setups_found, open_trades, week_count)` | Post-scan summary |
| `send_error(error_text)` | Error notification (truncated to 500 chars) |
| `send_startup(bal, symbols)` | Boot-up notification with config summary |
| `send_weekly_outlook(analyses)` | Formatted weekly outlook with bias, zones, key levels for all symbols. Auto-splits if > 3800 chars |

---

### 3.10 `trade_logger.py` — SQLite Trade Database

**Purpose:** Persistent trade and scan logging in SQLite (`trades.db`).

**Lines:** 189

#### Database Schema

**`trades` table:**
```sql
id, opened_at, closed_at, symbol, direction, grade, score, lot, entry,
sl, tp, rr, risk_usd, result_usd, ticket, status, tp1, tp2,
partial_closed, partial_profit
```

**`scans` table:**
```sql
id, scanned_at, symbol, grade, score, direction, action
```

#### Key Functions

| Function | Description |
|----------|-------------|
| `init_db()` | Creates tables if not exist |
| `log_trade_opened(setup, lot, risk_usd, ticket)` | Inserts new trade with status='open' |
| `log_trade_closed(ticket, result_usd)` | Updates status='closed', sets closed_at and result_usd |
| `log_partial_close(ticket, partial_profit)` | Sets `partial_closed=1` and stores partial profit |
| `is_partial_closed(ticket)` | Boolean check if TP1 partial has been taken |
| `log_scan(symbol, grade, score, direction, action)` | Records every scan result (for analytics) |
| `get_open_trade_tickets()` | Returns list of tickets with status='open' |
| `get_trade_by_ticket(ticket)` | Returns full trade dict by ticket number |
| `get_daily_trade_count()` | Counts trades opened today |
| `get_kill_zone_trade_count(kill_zone)` | Counts trades opened in today's session (note: currently counts all today's trades regardless of KZ) |
| `get_last_loss_time(symbol)` | Returns datetime of most recent losing trade on this symbol |
| `is_in_cooldown(symbol, cooldown_hours)` | Returns `(True, hours_remaining)` if the symbol had a loss within `cooldown_hours` (24h) |

---

### 3.11 `news_filter.py` — Economic News Filter

**Purpose:** Checks ForexFactory economic calendar for high-impact news events. Blocks entries 30 minutes before major events.

**Lines:** 92 | **Source:** `nfs.faireconomy.media/ff_calendar_thisweek.json`

| Function | Description |
|----------|-------------|
| `_load()` | Fetches and caches calendar JSON (1-hour TTL) |
| `has_high_impact_news_soon(symbol, minutes=30)` | Returns `(True, title)` if a High-impact event affecting the symbol's base/quote currency (or USD) is within `minutes`. Currency matching: extracts first 3 and next 3 chars of symbol, plus always includes USD |
| `get_todays_news()` | Returns formatted Telegram message listing all today's High-impact events, sorted by time |

**Fail-safe:** If the news check errors out, it returns `(False, None)` — never blocks trading on news API failure.

---

### 3.12 `weekly_analyst.py` — Weekend Top-Down Analysis

**Purpose:** Runs once on Saturday at `WEEKEND_ANALYSIS_HOUR` (08:00 UTC). Performs W1 → D1 → 4H analysis on every symbol and broadcasts a weekly outlook via Telegram.

**Lines:** 186

| Function | Description |
|----------|-------------|
| `analyze_symbol(symbol)` | Full top-down analysis: W1 bias (EMA21), D1 equilibrium position (premium/discount), nearest D1 AOI zone, PDH/PDL/PWH/PWL key levels, 4H bias. Combines into `overall_bias` via majority vote and `watch_direction` (LONG/SHORT/WAIT) |
| `run_all(symbols)` | Runs `analyze_symbol()` for all 13 symbols. Returns dated JSON with all analyses |
| `save(outlook)` / `load()` | Atomic JSON write/read for `weekly_outlook.json` |
| `already_ran_this_week()` | Checks if outlook was already generated this Saturday |

**Bias combination logic:** Majority vote from W1 bias, D1 position ("discount"→bullish), and 4H bias. Watch direction: LONG if bullish + in discount, SHORT if bearish + in premium, else WAIT.

---

### 3.13 `analytics.py` — Historical Trade Analytics

**Purpose:** Standalone CLI script that reads `trades.db` and prints performance statistics.

**Lines:** 84

Reports:
- Total closed trades, overall win rate, total P&L
- Performance by symbol (trades, win rate, P&L, avg duration)
- Performance by direction (bullish vs bearish)
- Longest winning and losing streaks

---

### 3.14 `backtester.py` — Multi-Timeframe Backtester

**Purpose:** Simulates the strategy on historical data with realistic partial TP, breakeven SL move, and full TP.

**Lines:** 162

**How it works:**
1. Fetches W1/D1/4H historical data for a symbol (default 2500 4H bars)
2. Walks forward bar-by-bar from bar 80 onward
3. **Manages open trade:** checks SL hit (P&L = −1R), TP1 hit (moves SL to BE), TP2 hit (P&L = 0.5×TP1_RR + 0.5×TP2_RR)
4. **Checks for new setups:** slices data up to current bar, calls `strategy.analyze_pair()`, opens on A+ or B+
5. **Reports:** Total trades, TP2 full targets, TP1+breakeven, full SL, win rate, total R return, profit factor

Can be run across all 13 symbols with a portfolio summary.

---

## 4. Data Flow: From Scan to Trade Execution

```
1. TRIGGER: 4H candle close (SCAN_HOURS) or Kill Zone 15-min tick
       │
2. PRE-FLIGHT CHECKS (bot.py → run_scan)
       ├── Balance ≥ $5?
       ├── Daily trades < 4?
       ├── Kill Zone trades < 2?
       ├── Concurrent positions < 2?
       │
3. PER-SYMBOL LOOP (13 symbols):
       ├── Cooldown check (24h post-loss)           → trade_logger.is_in_cooldown()
       ├── News check (30 min lookahead)             → news_filter.has_high_impact_news_soon()
       ├── Spread check (< 3× baseline)             → bridge_client.spread_ok()
       │
4. DATA FETCH
       ├── W1 candles (100 bars)                     → data_provider.get_weekly()
       ├── D1 candles (200 bars)                     → data_provider.get_daily()
       ├── 4H candles (200 bars)                     → data_provider.get_4h()
       └── 15M candles (200 bars, if Kill Zone)      → data_provider.get_15m()
               │
5. STRATEGY ANALYSIS                                 → strategy.analyze_pair()
       ├── Add EMA9/21/200, RSI, ATR, ADX to all TFs
       ├── ADX ≥ 20? (trending market filter)
       ├── Calculate swing range & OTE zones
       ├── Get W1/D1/4H biases
       ├── For each direction:
       │     ├── 4H EMA21 alignment check
       │     ├── TRUE OTE zone check
       │     ├── Count timeframe sync (2 or 3)
       │     ├── Find AOI zones (D1, W1, 4H)
       │     ├── Check triggers (engulfing/rejection/15M CHoCH)
       │     ├── Grade setup (0-131 point scoring)
       │     └── Return if A+ (≥95) or B+ (≥78, score≥80)
               │
6. GRADE GATE (bot.py)
       ├── A+ → proceed to AI gatekeeper
       └── B+ (score ≥ 80) → AI can upgrade if confidence ≥ 85
               │
7. DEDUPLICATION CHECK
       └── Same (direction, swing_eq) already signaled? → skip
               │
8. AI GATEKEEPER                                     → ai_engine.evaluate_setup()
       ├── Sends full setup + context to Gemini 2.5 Flash (or DeepSeek fallback)
       ├── Parses JSON: verdict, confidence, trap_detected, narrative
       ├── B+ upgrade: requires APPROVED + confidence ≥ 85 + no trap
       ├── A+ gate: rejects if trap detected, REJECTED verdict, or confidence < 70
       └── On approval: attaches AI verdict + narrative to setup
               │
9. LOT SIZING                                        → risk_manager.calculate_lot_size()
       ├── Target risk = max($1.50, 7.5% of balance), capped at $15
       ├── Lot = risk_usd / (SL_distance × tick_value)
       └── Clamp to broker min/max/step
               │
10. ORDER EXECUTION                                   → bridge_client.place_order()
       └── POST /order to mt5_bridge.py → MT5 market order
               │
11. LOGGING & NOTIFICATION
       ├── trade_logger.log_trade_opened()            → SQLite insert
       └── notifier.send()                            → Telegram with full details + AI badge
```

---

## 5. Trade Lifecycle Management

```
 TRADE OPENED
      │
      ├─── Every 15 seconds: manage_open_positions()
      │       │
      │       ├── TTL Check: open > 36h without TP1? → Force close
      │       │
      │       ├── TP1 Hit (price reaches 1:1.0 RR)?
      │       │     ├── Partial close 50% of position
      │       │     ├── Move SL to breakeven + spread buffer
      │       │     │     (BTC: $15, ETH: $1, XAU: $1, JPY: 0.04, Forex: 0.0004)
      │       │     ├── Set TP to TP2 (1:3.0 RR)
      │       │     └── Log partial close + notify Telegram
      │       │
      │       └── After TP1 (trailing stop active):
      │             ├── Fetch fresh 15M EMA21
      │             ├── Trail SL behind EMA21 ± 5 pip buffer
      │             └── Only trail in profit direction (above entry for longs)
      │
      ├─── Every 15 minutes: check_ema_invalidation()
      │       ├── Get last closed 4H candle
      │       ├── If close crossed EMA21 against direction → close early
      │       └── Notify Telegram
      │
      ├─── Every cycle: check_closed_positions()
      │       ├── Compare logged open tickets with live MT5 positions
      │       ├── If ticket missing from MT5 → fetch P&L from history
      │       ├── Log closure + notify Telegram
      │       └── Optional: AI post-trade review
      │
      └── TRADE CLOSED (by TP2, SL, TTL, EMA invalidation, or manual)
```

---

## 6. Telegram Command System

The bot supports both **slash commands** and **natural language/button taps**:

| Input | Action |
|-------|--------|
| `📊 Market Pulse` / `pulse` / `macro` | AI macro session commentary across all pairs |
| `🏦 Balance & Status` / `balance` / `account` / `status` / `/account` / `/status` | Show account info, balance, equity, leverage |
| `🥇 Gold` / `gold` / `xau` | AI deep analysis on XAUUSDm |
| `💶 EUR/USD` / `eurusd` / `eur` | AI deep analysis on EURUSDm |
| `₿ Bitcoin` / `bitcoin` / `btc` | AI deep analysis on BTCUSDm |
| `📈 P&L Report` / `report` / `profit` / `pnl` / `/report` | Today's and week's performance report |
| `📰 Today's News` / `news` / `/news` | High-impact economic calendar for today |
| `🔄 Switch Account` / `/real` / `/demo` | Toggle between demo and real account |
| `/help` / `help` / `menu` | Show command reference |
| `/ai [symbol]` | Deep SMC analysis on any tracked symbol |
| `/ai pulse` | Macro session commentary |
| `/ai status` | AI engine health check |
| `/ai [question]` | Freeform AI trading advisor |
| Any unrecognized text | Forwarded to AI as freeform question |

---

## 7. Main Event Loop Timing

```
while True:  (15-second sleep between iterations)
    │
    ├── EVERY CYCLE:
    │     └── check_closed_positions()
    │
    ├── EVERY 5 SECONDS:
    │     └── handle_telegram_commands()
    │
    ├── EVERY 15 SECONDS:
    │     └── manage_open_positions()  (TP1, trailing, TTL)
    │
    ├── EVERY 15 MINUTES:
    │     └── check_ema_invalidation()
    │
    ├── AT 4H CANDLE CLOSES (00, 04, 08, 12, 16, 20 UTC, minute 0):
    │     └── run_scan(kill_zone=None)
    │
    ├── DURING KILL ZONES (every 15-min slot):
    │     ├── London:   07:00 - 10:00 UTC
    │     ├── New York: 12:00 - 15:00 UTC
    │     ├── Asian:    00:00 - 04:00 UTC
    │     └── run_scan(kill_zone="london"|"new_york"|"asian")
    │
    ├── DAILY AT 06:00 UTC:
    │     └── Auto-broadcast today's high-impact news
    │
    └── SATURDAY AT 08:00 UTC:
          └── weekly_analyst.run_all() → Weekend outlook broadcast
```

---

## 8. Grading & Quality Gate System

```
          ┌─────────────────────────────────────────┐
          │         STRATEGY ANALYSIS                │
          │    (strategy.analyze_pair)               │
          │         Score: 0 - ~131                  │
          └─────────────┬───────────────────────────┘
                        │
               ┌────────┴────────┐
               │                 │
          Score ≥ 95        Score ≥ 78
          Grade: A+         Grade: B+
               │            (score ≥ 80)
               │                 │
               ▼                 ▼
        ┌──────────┐     ┌──────────────┐
        │ AI Gate  │     │ AI Upgrade   │
        │ min 70%  │     │ needs ≥85%   │
        │ conf     │     │ conf + no    │
        │ no trap  │     │ trap + APPR  │
        └────┬─────┘     └──────┬───────┘
             │                  │
             ▼                  ▼
        EXECUTE TRADE      EXECUTE TRADE
       (if APPROVED)    (grade → "A+ (AI)")
```

---

## 9. Risk Management Architecture

| Layer | Rule | Implementation |
|-------|------|----------------|
| **Balance Floor** | Pause trading if balance < $5 | `bot.py → run_scan()` checks `config.MIN_BALANCE` |
| **Daily Trade Limit** | Max 4 trades per day | `trade_logger.get_daily_trade_count()` |
| **Kill Zone Limit** | Max 2 trades per KZ session | `trade_logger.get_kill_zone_trade_count()` |
| **Concurrent Limit** | Max 2 open positions | `bridge.get_positions()` check |
| **Post-Loss Cooldown** | 24h cooldown per symbol after a loss | `trade_logger.is_in_cooldown()` |
| **Spread Filter** | Skip if spread > 3× normal baseline | `bridge_client.spread_ok()` |
| **News Filter** | Skip 30 min before high-impact events | `news_filter.has_high_impact_news_soon()` |
| **ADX Regime** | Skip if ADX < 20 (ranging/choppy) | `strategy.py → analyze_pair()` |
| **OTE Zone** | Longs only in discount, shorts only in premium | `strategy.is_price_in_favorable_zone()` |
| **Grade Gate** | Only A+ (≥95 pts) or AI-upgraded B+ | `bot.py → run_scan()` |
| **AI Gatekeeper** | Dual-AI trap detection + institutional confirmation | `ai_engine.evaluate_setup()` |
| **Risk Sizing** | 7.5% of balance per trade, floor $1.50, cap $15 | `risk_manager.calculate_lot_size()` |
| **Percentage Cap** | Never risk > 10% of balance on a single trade | Risk manager clamps lot if exceeded |
| **Setup Dedup** | Same direction+swing → skip | `_last_signaled` dict in `bot.py` |
| **Trade TTL** | Force-close if TP1 not hit in 36h | `manage_open_positions()` |
| **EMA Invalidation** | Close early if 4H candle breaks EMA21 structure | `check_ema_invalidation()` |

---

## 10. AI Gatekeeper Pipeline

```
┌──────────────┐
│ Candidate    │
│ Setup        │
│ (A+ or B+)   │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────┐
│  Build AI Prompt:                     │
│  - Symbol, Direction, Entry, SL, TP  │
│  - RR ratio, Grade, Score            │
│  - Trigger type, ADX                 │
│  - Confluences list                  │
│  - RSI, Spread, News status          │
└──────────────┬───────────────────────┘
               │
               ▼
┌───────────────────────┐     FAIL     ┌────────────────────┐
│  Google Gemini 2.5    │────────────▶│  OpenRouter         │
│  Flash (Primary)      │              │  DeepSeek (Fallback)│
└───────────┬───────────┘              └──────────┬─────────┘
            │                                      │
            └──────────────┬───────────────────────┘
                           │
                    Parse JSON Response
                           │
              ┌────────────┴────────────┐
              │                         │
         A+ Setup                 B+ Setup
              │                         │
     ┌────────┴────────┐       ┌────────┴──────────┐
     │ verdict=REJECTED │       │ verdict≠APPROVED  │
     │ OR trap=true     │       │ OR conf < 85      │
     │ OR conf < 70     │       │ OR trap=true      │
     └────────┬─────────┘       └────────┬──────────┘
              │YES                       │YES
              ▼                          ▼
         SKIP TRADE                 SKIP TRADE
        (notify TG)               (no upgrade)
              │NO                        │NO
              ▼                          ▼
         EXECUTE                   UPGRADE to A+
                                   then EXECUTE
```

---

## 11. File Inventory

### Core Production Files (10 files)

| File | Lines | Size | Role |
|------|-------|------|------|
| `bot.py` | 827 | 35.5 KB | Main orchestrator, event loop, scan engine, position manager, Telegram handler |
| `strategy.py` | 693 | 26.9 KB | Full ICT/SMC strategy: indicators, AOI, OTE, triggers, grading, SL/TP |
| `ai_engine.py` | 308 | 12.9 KB | Dual-AI engine (Gemini + OpenRouter), gatekeeper, analysis, chat |
| `mt5_bridge.py` | 465 | 16.2 KB | MT5 HTTP bridge server, order execution, account management |
| `bridge_client.py` | 152 | 4.4 KB | HTTP client for MT5 bridge |
| `config.py` | 128 | 4.6 KB | All configuration parameters |
| `notifier.py` | 270 | 9.5 KB | Telegram bot: send, poll, keyboard, formatted messages |
| `trade_logger.py` | 189 | 5.5 KB | SQLite trade/scan logging, cooldown tracking |
| `news_filter.py` | 92 | 3.3 KB | ForexFactory high-impact news filter |
| `data_provider.py` | 75 | 2.2 KB | Market data fetching via bridge OHLCV endpoint |

### Supporting Modules (3 files)

| File | Lines | Size | Role |
|------|-------|------|------|
| `weekly_analyst.py` | 186 | 6.8 KB | Saturday weekend top-down analysis |
| `analytics.py` | 84 | 2.7 KB | CLI trade performance analytics |
| `backtester.py` | 162 | 6.0 KB | Historical strategy backtesting engine |
| `risk_manager.py` | 123 | 4.6 KB | Position sizing and risk calculations |

---

## 12. Utility & Debug Scripts

| Script | Lines | Purpose |
|--------|-------|---------|
| `check_spreads.py` | 21 | Prints live spread vs config baseline for all 13 symbols |
| `debug_crypto.py` | 41 | Dumps BTC/ETH indicators, AOI zones, equilibrium for debugging |
| `debug_engulf.py` | 38 | Checks engulfing patterns on all symbols with last 3 candle dumps |
| `full_grade_scan.py` | 61 | Scans all symbols showing grade prerequisites (4H bias, AOI, EMA21) |
| `trigger_levels.py` | 47 | Shows what price needs to do for each symbol to generate a trigger |
| `test_scan.py` | 34 | Runs `strategy.analyze_pair()` on all symbols with lot sizing |
| `test_signals.py` | 178 | Full pipeline test: bridge, data feeds, indicators, bias, swing/OTE, AOI, news, risk manager, strategy scan, trade logger |
| `test_entry_points.py` | 113 | Tests entry trigger functions (CHoCH, rejection, engulfing), SL/TP engine, AI gatekeeper, lot sizing |
| `test_ai_analysis.py` | 55 | Tests AI deep pair analysis on EURUSDm with real market data |
| `live_demo_test.py` | 122 | End-to-end live test on demo: places 0.01 lot order, checks MT5, AI evaluation, closes, AI review |
| `speed_benchmark.py` | 59 | Benchmarks latency: bridge ping, data fetch, Gemini/OpenRouter response, full scan estimate |
| `verify_all.py` | 65 | Import verification + AI connectivity test + setup evaluation test |
| `profit_500.py` | 42 | Projects P&L on $500 balance across 5 sample setups |
| `profit_calc.py` | 53 | Real-time profit projection using live balance and market data |
| `send_announcement.py` | 19 | One-shot Telegram announcement (AI engine launch) |
| `send_menu.py` | 20 | One-shot Telegram message introducing the interactive keyboard menu |
| `patch_bot.py` | 75 | Historical patch script: EMA invalidation auto-close + stale re-entry guard |
| `patch_bot2.py` | 73 | Alternative version of the same patches with byte-exact matching |
| `health_check.sh` | 136 | Bash script: checks systemd services, bridge health, account, positions, symbols, OHLCV, news, PnL, autotrading config, Telegram, bot config, recent logs |

---

## 13. Configuration Data Files

| File | Format | Purpose |
|------|--------|---------|
| `active_account.json` | `{"active_account": "demo"}` | Persists which account profile (demo/real) is active |
| `chat_id.json` | `{"chat_id": 123456}` | Persists the Telegram chat ID after first `/start` |
| `tg_offset.json` | `{"offset": 123456}` | Tracks last processed Telegram update ID |
| `weekly_outlook.json` | Full analysis JSON | Saturday weekend analysis results (used to prevent re-runs) |
| `trades.db` | SQLite | Trade log and scan history database |

---

## Inter-Module Dependency Graph

```
config.py ──────────▶ (imported by ALL modules)
                          │
bot.py ─────────────────┬─▶ strategy.py
                        ├─▶ ai_engine.py
                        ├─▶ risk_manager.py ──▶ bridge_client.py
                        ├─▶ bridge_client.py ──▶ config.BRIDGE_URL
                        ├─▶ data_provider.py ──▶ config.BRIDGE_URL
                        ├─▶ trade_logger.py  ──▶ trades.db
                        ├─▶ news_filter.py   ──▶ ForexFactory API
                        ├─▶ notifier.py      ──▶ Telegram API
                        └─▶ weekly_analyst.py ──▶ data_provider, strategy

mt5_bridge.py ──────────▶ MetaTrader5 (standalone process)

ai_engine.py ───────────▶ Gemini API + OpenRouter API (no local deps)
```

---

> **Total codebase:** ~3,400 lines of production Python across 14 modules, plus ~1,100 lines of utility/test scripts across 18 helper files.
