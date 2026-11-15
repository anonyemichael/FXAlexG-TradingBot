# FXAlexG Institutional Algorithmic Trading Engine (v3.3)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform MT5](https://img.shields.io/badge/platform-MetaTrader%205-green.svg)](https://www.metatrader5.com/)
[![Runtime Ubuntu/Systemd](https://img.shields.io/badge/runtime-Ubuntu%20Linux%20%7C%20Systemd-orange.svg)](https://ubuntu.com/)
[![AI Engine Gemini 2.5](https://img.shields.io/badge/AI%20Engine-Google%20Gemini%202.5%20Flash-purple.svg)](https://deepmind.google/technologies/gemini/)
[![Architecture Headless Bridge](https://img.shields.io/badge/architecture-REST%20HTTP%20Bridge-lightgrey.svg)]()

> **Institutional-grade automated trading system combining daily momentum trend-following, ICT/SMC multi-timeframe structural liquidity analysis, Google Gemini AI setup evaluation, and automated multi-account MetaTrader 5 execution.**

---

## 1. Executive Summary

**FXAlexG v3.3** is an institutional-grade, event-driven algorithmic trading engine and multi-account portfolio orchestrator. Engineered in Python to run 24/7 on headless Linux environments (Wine + Xvfb), the system interfaces directly with MetaTrader 5 via an isolated HTTP REST bridge service.

The architecture decouples market data extraction, quantitative signal generation, AI analytical gating, capital risk verification, and broker order execution. It supports concurrent multi-account execution across heterogeneous strategies—ranging from daily crypto trend following to mechanical 4-hour / 15-minute Inner Circle Trader (ICT) Smart Money Concepts (SMC).

```
                            +-------------------------------------------+
                            |       MetaTrader 5 Broker Terminal        |
                            |       (Wine / Xvfb Headless Daemon)       |
                            +---------------------+---------------------+
                                                  | Native Python API
                                                  v
                            +-------------------------------------------+
                            |     Flask HTTP Bridge (mt5_bridge.py)     |
                            |      Port 5001 - Low-Latency REST API     |
                            +---------------------+---------------------+
                                                  ^
                                                  | JSON / HTTP
                                                  v
+---------------------------------------------------------------------------------------------------+
|                                  FXAlexG Orchestration Engine                                    |
|                                                                                                   |
|  +-----------------------+     +------------------------+     +--------------------------------+  |
|  |     Strategy Core     |     |   AI Valuation Gate    |     |      Risk Management Gate      |  |
|  | - Daily Trend (EMA)   | --> | - Gemini 2.5 Flash     | --> | - Account-Sized Lots           |  |
|  | - ICT / SMC Engine    |     | - JSON Strict Contract |     | - Downward Lot Rounding        |  |
|  | - Multi-TF Structure  |     | - Macro Sentiment      |     | - Hard Drawdown Protection     |  |
|  +-----------------------+     +------------------------+     +---------------+----------------+  |
|                                                                               |                   |
|  +----------------------------------------------------------------------------v----------------+  |
|  |                                Multi-Account Portfolio Router                               |  |
|  |              Dispatches validated orders to assigned account tiers in accounts.db           |  |
|  +--------------------------------------------+------------------------------------------------+  |
+-----------------------------------------------|---------------------------------------------------+
                                                |
                     +--------------------------+--------------------------+
                     |                                                     |
                     v                                                     v
   +------------------------------------+                +------------------------------------+
   |     SQLite State Persistence       |                |     Telegram Telemetry Matrix      |
   |  - trades.db (Execution records)   |                |  - Real-time trade event badges    |
   |  - accounts.db (Multi-tenant)      |                |  - Interactive command terminal    |
   +------------------------------------+                +------------------------------------+
```

---

## 2. Quantitative Strategy & Alpha Models

The platform implements two distinct, mathematically backtested execution engines designed to extract alpha across non-correlated asset classes and regimes:

### 2.1 Engine A: Daily Crypto Trend-Following (`trend` & `trend_fast`)
Designed for liquid cryptocurrency pairs to capture high-beta macro directional expansions while strictly controlling downside exposure.

* **Mathematical Rules:**
  * **Regime Filter:** Daily close above Exponential Moving Averages ($EMA_{fast} > EMA_{slow}$).
  * **Long-Only Bias:** Exploits the structural long-term asymmetry of crypto assets; short-selling was rigorously backtested and declined due to poor edge ($PF \approx 0.76$).
  * **Dynamic Stop-Loss:** Calculated as entry minus $3.0 \times ATR(20)_{daily}$. The trailing stop is ratcheted upward once daily at market close, locking in uncaptured unrealized profit.
  * **Exit Condition:** Clean daily close below the dynamic EMA baseline or trailing stop violation.
* **Variant Comparison:**
  * `trend` (Standard): Daily EMA 20 / EMA 100 crossover. Lower frequency (~9 trades/yr across 7 coins), exceptional statistical reliability.
  * `trend_fast`: Daily EMA 15 / EMA 80 crossover. Higher turnover (~13 trades/yr), tighter response with wider variance.
* **Universe Allocation:**
  * *Micro Accounts ($20–$100):* `SOLUSDm`, `XRPUSDm`, `ADAUSDm`, `DOGEUSDm`. Assets with granular broker lot sizing enabling valid risk scaling below account risk caps.
  * *Standard Accounts ($500+):* `BTCUSDm`, `ETHUSDm`, `BNBUSDm`. Automatically unlocked when equity permits minimum lot sizes without violating risk parameters.

### 2.2 Engine B: Institutional ICT / Smart Money Concepts (`smc` & `smc_ai`)
Mechanical implementation of institutional order-flow microstructure principles targeting intraday and swing liquidity imbalances.

* **Multi-Timeframe Structural Stack:**
  * **Weekly & Daily (W1/D1):** High-Timeframe (HTF) institutional bias and structural order flow.
  * **4-Hour (4H):** Dealing range identification, Premium vs. Discount valuation (entries restricted to $\le 50\%$ discount for longs, $\ge 50\%$ premium for shorts), and Areas of Interest (AOI).
  * **15-Minute (15M):** Execution timeframe. Identifies Change of Character (CHoCH), Fair Value Gaps (FVG), breaker blocks, and liquidity sweeps during London / New York Kill Zones.
* **Scoring & Grading Gate:**
  * **A+ Setups (Score $\ge 95$):** Full multi-timeframe alignment, clean liquidity sweep, prime discount/premium displacement. Executed mechanically across all SMC accounts.
  * **B+ Setups (Score $80–94$):** Requires AI confirmation.
* **Curated 5-Asset Universe:**
  * `BTCUSDm`, `EURJPYm`, `GBPUSDm`, `GBPJPYm`, `ETHUSDm`.
  * *Gold (`XAUUSDm`) Decommission Notice:* Real MT5 execution history and tick data analysis demonstrated that Gold's high volatility and 0.01 minimum lot size produced disproportionate drawdown on accounts under $1,000. It has been isolated from micro-account profiles.

---

## 3. Empirical Research & Walk-Forward Validation

Every strategy variant in FXAlexG undergoes strict walk-forward backtesting using out-of-sample data partitions (first 60% sample vs. final 40% out-of-sample).

| Quantitative Model | Sample Window | Sample Size | Aggregate PF | Out-of-Sample PF | Production Verdict |
|---|---|---|---|---|---|
| **Crypto Trend (7 Coins, EMA 20/100)** | 2019 – 2026 | 73 trades | **3.41** | **6.67** | **Active Production** |
| **Crypto Trend Fast (7 Coins, EMA 15/80)** | 2019 – 2026 | 104 trades | **2.31** | **4.09** | **Active Production** |
| **Crypto Trend (4 Core Micro Coins)** | 2019 – 2026 | 37 trades | **5.18** | **10.67** | **Active Production** |
| Original SMC (Forex Majors) | 2022 – 2026 | 379 trades | 0.71 | 0.44 | Deprecated (Negative Edge) |
| Forex Daily Trend (Long Only) | 2019 – 2026 | 120 trades | 0.57 | 0.60 | Deprecated (Negative Edge) |
| London Breakout (1R Target) | 2020 – 2026 | 8,729 trades | 1.00 | 1.00 | Rejected (Zero Edge Post-Spread) |
| London Breakout (2R Target) | 2020 – 2026 | 8,729 trades | 1.04 | 1.04 | Rejected (Fails Commission/Slippage) |

### Micro-Capital Compounding Curves ($20 Initial Balance, Trend Engine)
Simulated historical compounding on non-reinvested fixed fractional risk per trade:
* **2.0% Risk / Trade:** Final Balance: **$68** | Max Drawdown: **14%**
* **5.0% Risk / Trade:** Final Balance: **$139** | Max Drawdown: **25%**
* **10.0% Risk / Trade (Live Profile):** Final Balance: **$478** | Max Drawdown: **46%** (Trough Equity: $12.08)

---

## 4. Multi-Account Portfolio Router

FXAlexG features an integrated multi-tenant account dispatcher managed through `accounts.db`:

```
                                      +------------------------+
                                      | Strategy Signal Raised |
                                      +-----------+------------+
                                                  |
                         +------------------------+------------------------+
                         |                                                 |
                         v                                                 v
           [Strategy: "trend"]                               [Strategy: "smc"]
                         |                                                 |
           +-------------+-------------+                                   |
           |                           |                                   |
           v                           v                                   v
+--------------------+      +--------------------+              +--------------------+
| Master Account     |      | Follower Account   |              | Dedicated Account  |
| Login: 436015341   |      | Login: 486074840   |              | Login: 477674441   |
| Strat: trend       |      | Strat: trend_fast  |              | Strat: smc         |
| Risk: 10%          |      | Risk: 10%          |              | Risk: 10%          |
+--------------------+      +--------------------+              +--------------------+
```

* **Session Context Switching:** The MT5 bridge maintains a persistent connection to the master account. When executing or auditing secondary accounts, the bridge dynamically switches terminal login contexts, executes the order pipeline, verifies ticket acquisition, and restores the primary session.
* **Granular Risk Quotas:** Each account in `accounts.db` maintains independent balance tracking, custom risk allocations (`risk_pct`), strategy assignments, and active/paused state toggles.

---

## 5. Risk Management & Execution Safeguards

1. **Broker-Side Execution Only:** Every trade is transmitted with hard broker-side Stop Loss (SL) and Take Profit (TP) parameters. No position is left unhedged on the client side.
2. **Dynamic Downward Lot Rounding:** On micro accounts ($20–$100), standard mathematical lot rounding up can unintentionally risk 30–50% of capital on broker minimum lot limits (0.01). FXAlexG enforces strict downward rounding: if minimum lot size exceeds the calculated maximum risk dollar cap (`MAX_RISK_PCT`), the trade is automatically aborted.
3. **Adaptive Take-Profit Ladder:**
   * **TP1 ($1.5 \times R$):** Closes 50% of the position volume and updates the broker-side SL to entry breakeven. (Automatically adjusted to breakeven-only if lot size is at the broker's 0.01 minimum).
   * **TP2 ($3.5 \times R$):** Final profit target target for SMC structural extensions.
4. **Spread Spike Shield:** Rejects entry execution when current bid/ask spread exceeds $3.0 \times$ normal baseline spread.
5. **Economic Calendar Blackout:** Integrated ForexFactory news parser blocks new order execution 30 minutes prior to and 30 minutes following high-impact (Red Folder) macroeconomic announcements.
6. **Time-to-Live (TTL) Safety Invalidation:** Positions failing to reach TP1 within 36 hours undergo systematic health evaluation to mitigate weekend swap degradation and range stagnation.

---

## 6. AI Gatekeeper Engine (Google Gemini 2.5 Flash)

FXAlexG integrates Google Gemini 2.5 Flash as an institutional evaluation layer:

* **Strict JSON Contract Enforcement:** Employs `responseMimeType: "application/json"` with `thinkingBudget: 0` to prevent token exhaustion and JSON truncation during high-volatility market pulses.
* **Dual Gate Modes:**
  * `advisory` *(Production Default)*: Attaches AI institutional rationale to Telegram alerts while allowing pure mathematical strategy rules to determine execution. Ensures zero dropped trades during cloud API outages.
  * `veto`: AI acts as a hard filter; setups are rejected if institutional confidence falls below 70%.
* **Automated Post-Trade Review:** Evaluates closed orders against HTF macro context to build persistent learning memories in `trades.db`.

---

## 7. System Architecture & Topology

```text
.
├── account_manager.py      # SQLite accounts.db multi-tenant manager
├── ai_engine.py            # Gemini 2.5 Flash client & JSON schema enforcement
├── ai_trainer.py           # Institutional meta-learning & feedback optimizer
├── analytics.py            # Trade performance analytics & expectancy reporting
├── backtester.py           # Historical backtest engine for SMC logic
├── bot.py                  # Main system event loop & scheduler daemon
├── bridge_client.py        # Low-latency REST client with automatic order recovery
├── check_spreads.py        # Real-time broker spread audit tool
├── config.py               # Central environment & quantitative parameter configuration
├── data_provider.py        # Multi-timeframe OHLCV aggregator & feature pipeline
├── diagnose_all.py         # End-to-end multi-account diagnostic engine
├── full_grade_scan.py      # Instantaneous market-wide setup scan utility
├── london_research.py      # London / New York breakout quantitative research
├── mt5_bridge.py           # Headless Flask REST bridge interacting with MT5 API
├── news_filter.py          # Economic calendar parser & news blackout gate
├── notifier.py             # Telegram bot messaging hub & interactive inline keyboard
├── risk_manager.py         # Capital protection, lot calculation, and margin profiler
├── strategy.py             # ICT / SMC structural market analysis & grading engine
├── test_ai_analysis.py     # AI gatekeeper unit test harness
├── trade_logger.py         # SQLite trades.db state persistence & scoreboard engine
├── trend_research.py       # Crypto trend quantitative research & parameter sweep
├── trend_strategy.py       # Daily EMA20/100 trend-following production strategy
├── verify_all.py           # Production pre-flight readiness audit
├── walkforward_backtest.py # Out-of-sample walk-forward backtest harness
├── FULL_SYSTEM_DOCS.md     # Deep architecture and systems manual
├── SYSTEM_GUIDE.md         # Operational handbook and production guide
└── CHANGELOG_2026-11-14.md # Infrastructure & VPS migration log
```

---

## 8. Installation & Deployment Guide

### 8.1 Prerequisites
* Ubuntu 20.04 / 22.04 LTS (Oracle Cloud Always-Free tier fully supported)
* Wine 64-bit with MetaTrader 5 installed
* Python 3.10+
* Dedicated Telegram Bot token ([@BotFather](https://t.me/BotFather))
* Google Gemini API Key

### 8.2 Environment Configuration
Clone the repository and initialize the environment file:

```bash
git clone https://github.com/anonyemichael/FXAlexG-TradingBot.git
cd FXAlexG-TradingBot
cp .env.example .env
```

Populate `.env` with your secure credentials:

```dotenv
# Core Telegram Configuration
TELEGRAM_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxyz
TELEGRAM_CHAT_ID=123456789

# AI Gateway
GEMINI_API_KEY=AIzaSy...

# MT5 REST Bridge Configuration
BRIDGE_URL=http://127.0.0.1:5001

# Master MT5 Broker Credentials
MT5_DEMO_LOGIN=436015341
MT5_DEMO_PASSWORD=YourSecurePassword
MT5_DEMO_SERVER=Exness-MT5Trial9
ACTIVE_ACCOUNT=demo
```

### 8.3 Systemd Production Service Setup
To ensure automatic failover, restarts upon reboot, and OOM isolation, configure the system as managed Linux daemons:

```bash
# 1. Virtual Display Daemon for Headless Wine
sudo systemctl enable --now xvfb.service

# 2. MetaTrader 5 Terminal Instance
sudo systemctl enable --now mt5-terminal.service

# 3. Low-Latency MT5 REST Bridge
sudo systemctl enable --now mt5-bridge.service

# 4. FXAlexG Trading Engine
sudo systemctl enable --now fxalexg-bot.service
```

---

## 9. Telemetry & Telegram Command Terminal

FXAlexG features an interactive command matrix through Telegram. All outgoing order alerts prepend explicit bot badges (e.g., `📈 Crypto Trend · #436015341` or `🎯 SMC · #477674441`).

| Command | Action |
|---|---|
| `/scoreboard` | Real-time balance refresh, closed-trade P&L, win-rate ranking across all accounts |
| `/status` | Active connection health, open tickets, MT5 bridge latency |
| `/report` | Cumulative P&L breakdown, Sharpe ratio, and expectancy metrics |
| `/accounts` | Enumerate all connected accounts and assigned strategies |
| `/strategy <login> <name>` | Hot-swap account strategy (`trend`, `trend_fast`, `smc`, `smc_ai`) |
| `/risk <login> <pct>` | Adjust per-trade risk percentage dynamically |
| `/pause <login>` | Suspend new order scanning for a specific account |
| `/resume <login>` | Re-activate order execution for a paused account |
| `/pulse` | Trigger instantaneous macro market sentiment sweep |
| `/news` | Display impending high-impact economic events |

---

## 10. Operational Health & Troubleshooting

```bash
# View live orchestrator stream
sudo journalctl -u fxalexg-bot -f

# Inspect bridge network communications
sudo journalctl -u mt5-bridge -n 100 --no-pager

# Perform comprehensive pre-flight verification
python3 verify_all.py

# Run standalone quantitative strategy simulation
python3 trend_research.py
```

---

## 11. Risk Disclaimer

**DISCLAIMER:** Automated trading in financial instruments, foreign exchange (Forex), and cryptocurrencies involves significant risk of loss and is not suitable for all investors. Historical backtest performance is not indicative of future returns. The software is provided "as is", without warranty of any kind. Carefully evaluate your financial status and risk tolerance prior to live capital deployment.

---

## 12. License

Distributed under the MIT License. See `LICENSE` for details.
