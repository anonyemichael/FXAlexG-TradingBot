# FXAlexG Trading Bot

Institutional FXAlexG ICT trading engine with autonomous AI-assisted execution, multi-timeframe SMC scanning, and multi-account MT5 orchestration.

This project is a Python-based automated FX trading system designed to scan the market, evaluate structured setups using ICT / Smart Money Concepts, apply AI validation, and execute orders through MetaTrader 5.

## Overview

FXAlexG combines:

- Multi-timeframe market scanning across W1, D1, H4, and M15 data
- ICT/SMC trade logic including swing structure, OTE/discount-premium logic, liquidity sweeps, and trigger detection
- AI-assisted setup evaluation with Gemini/OpenRouter fallback
- MT5 bridge integration for live execution and account switching
- Telegram notifications and interaction
- SQLite trade logging and analytics
- Risk controls, cooldowns, spread checks, and news filtering

The system is intended for research, strategy testing, and controlled automation in a broker/terminal environment. It is not a turnkey guarantee of profit and should be treated as a professional trading tool requiring thoughtful setup and monitoring.

## Key Features

- Institutional-grade multi-timeframe structure analysis
- Symbol coverage for major FX pairs plus crypto and XAUUSD
- A+ / B+ grading gate with AI approval path
- Dynamic lot sizing and risk management
- MT5 HTTP bridge architecture for safe execution separation
- Telegram bot interface for market pulse, reports, and status
- Trade lifecycle handling: TP1, breakeven, trailing stop, TTL, invalidation
- News and spread filters to reduce poor-entry conditions
- Backtesting and diagnostics utilities

## Architecture

```text
+-------------------+      +---------------------+
| Market Data       | ---> | Strategy Engine     |
| MT5 / OHLCV       |      | (ICT/SMC logic)     |
+-------------------+      +----------+----------+
                                         |
                                         v
                               +-------------------+
                               | AI Gatekeeper     |
                               | Gemini/OpenRouter |
                               +----------+--------+
                                          |
                                          v
                              +----------------------------+
                              | Risk + Execution Layer     |
                              | - lot sizing               |
                              | - spread/news filters      |
                              | - MT5 bridge              |
                              +-------------+--------------+
                                            |
                                            v
                              +----------------------------+
                              | Trade Manager + Logger     |
                              | - positions                |
                              | - database                |
                              | - Telegram alerts          |
                              +----------------------------+
```

## Project Structure

```text
.
├── .env.example                # Sample environment configuration
├── .gitignore                  # Git ignore rules
├── README.md                   # This file
├── FULL_SYSTEM_DOCS.md         # Expanded project documentation
├── SYSTEM_MANUAL.md            # Operational manual
├── SYSTEM_HEALTH_REPORT.md     # Health / validation notes
├── V3.2_UPDATE_LOG.md          # Update history
├── account_manager.py          # Account management logic
├── ai_engine.py                # AI evaluation + chat + provider routing
├── ai_trainer.py               # AI training / tuning helpers
├── analytics.py                # Summary reporting over trade DB
├── backtester.py               # Historical strategy backtesting
├── bot.py                      # Main orchestration engine
├── bridge_client.py            # MT5 bridge HTTP client
├── config.py                  # Core settings and environment-based config
├── data_provider.py            # OHLCV data fetcher
├── mt5_bridge.py               # MT5 HTTP bridge server
├── news_filter.py              # ForexFactory / economic news checks
├── notifier.py                 # Telegram messaging and keyboard handlers
├── risk_manager.py             # Position sizing and safety checks
├── strategy.py                 # Core ICT/SMC analysis and grading
├── trade_logger.py             # SQLite logging and state tracking
├── weekly_analyst.py           # Weekly macro outlook generation
├── health_check.sh             # Operational system validation script
├── *.py                        # Utility, scan, verify, and debug scripts
├── trades.db                   # Runtime SQLite DB (generated)
├── weekly_outlook.json         # Generated outlook cache
├── active_account.json         # Generated runtime config
├── chat_id.json                # Telegram chat ID cache
├── tg_offset.json              # Telegram offset cache
└── .env                        # Local secrets; not committed
```

## Requirements

- Python 3.10+
- MetaTrader 5 terminal and account access
- MT5 Python package (`MetaTrader5`)
- Telegram Bot token
- Gemini API key and/or OpenRouter API key
- Network access to broker / Telegram / AI endpoints
- A machine that can run the bridge process with MT5 installed

## Environment Setup

1. Copy the sample environment file:

```bash
cp .env.example .env
```

2. Fill in your credentials and broker details in `.env`.

Example variables include:

```dotenv
TELEGRAM_TOKEN=your_telegram_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
BRIDGE_URL=http://localhost:5001
MT5_DEMO_LOGIN=00000000
MT5_DEMO_PASSWORD=
MT5_DEMO_SERVER=Exness-MT5Trial7
MT5_REAL_LOGIN=00000000
MT5_REAL_PASSWORD=
MT5_REAL_SERVER=Exness-MT5Real15
ACTIVE_ACCOUNT=demo
```

3. Review and tune the bot in `config.py` for symbols, risk, spreads, scan schedule, and strategy filters.

## Quick Start

### 1) Launch the MT5 bridge

Run the bridge process on the machine where MetaTrader 5 is installed:

```bash
python mt5_bridge.py
```

### 2) Start the trading bot

```bash
python bot.py
```

This starts the event loop, Telegram command handling, scanning, trade management, and logging.

### 3) Optional: run diagnostics

```bash
python verify_all.py
python test_scan.py
python analytics.py
```

## Core Runtime Flow

The bot typically works like this:

1. Polls MT5 health / account status
2. Checks open trades and lifecycle rules
3. Runs market scans at configured 4H or kill-zone intervals
4. Evaluates setups using ICT/SMC logic and grading
5. Applies AI gatekeeper approval when enabled
6. Checks risk, spread, and news filters
7. Executes order through MT5 bridge
8. Logs trades and sends Telegram updates

## Trading Logic Highlights

- Multi-timeframe structure bias and trend alignment
- OTE / discount-premium zone validation
- AOI zones, key levels, CHoCH, engulfing, and rejection candle checks
- ADX-based trend filtering
- Risk-based execution only for qualified setups
- Trade lifecycle automation for partial exits and trailing management

## Risk and Safety

This project includes safety systems such as:

- Daily trade caps
- Concurrent position caps
- Symbol cooldown after losses
- Spread and news checks
- Maximum risk percentage guardrails
- EMA-based trade invalidation logic
- TTL-based exit protection

However, live trading still carries substantial financial risk. Use this project only in a controlled environment and validate with backtesting and paper trading before live execution.

## Backtesting and Analysis

Useful tools in the repo:

```bash
python backtester.py
python analytics.py
python full_grade_scan.py
python debug_engulf.py
python test_signals.py
```

These help evaluate historical behavior, signal quality, and trade classification.

## Documentation

This repository includes several notes and reference docs:

- `FULL_SYSTEM_DOCS.md` — deep architecture and system documentation
- `SYSTEM_MANUAL.md` — operational instructions and workflows
- `README_BOT_STRUCTURE.md` — bot structure overview
- `V3.2_UPDATE_LOG.md` — version update notes
- `SYSTEM_HEALTH_REPORT.md` — health and validation summaries

## Important Notes

- The repo is designed around real MT5 execution and is not a generic demo app.
- Keep `.env` local and never commit password or API secret values.
- Review the code and strategy before using it with real capital.
- MT5 connectivity, market hours, and broker rules differ by account and region.

## Disclaimer

This project is provided for educational and research purposes. It does not constitute financial advice. Automated trading may result in loss of capital. The author and contributors are not responsible for losses incurred while using this software.

## License

This repository does not currently declare an explicit license in the GitHub metadata. If needed, add a license file before production or distribution use.

## Contributing

If you are extending the project, keep these principles in mind:

- prefer clear, well-commented changes
- preserve safety checks and risk controls
- validate with targeted unit or functional tests
- keep `.env` and secrets out of version control

---

Built for institutional-style FX automation, AI-assisted analysis, and disciplined execution workflows.
