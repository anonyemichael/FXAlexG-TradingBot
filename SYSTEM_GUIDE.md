# FXAlexG Trading Bot — System Guide (updated 2026-11-03)

Online copy: https://claude.ai/artifact/Rt67RcpbPuTbJE266YwvfE
Copies of this file: `TradingBotCode/TradingBotCode/SYSTEM_GUIDE.md` (PC) and `/home/ubuntu/fxalexg_bot/SYSTEM_GUIDE.md` (VPS).

## 1. Live status (verified 2026-11-03 11:13 UTC)
- VPS services `xvfb`, `mt5-terminal`, `mt5-bridge`, `fxalexg-bot`: enabled at boot, active, `Restart=always`.
- MT5 bridge connected to the main account 436015341 (balance $24.47).
- Telegram: token and chat ID set, 0 errors in the last hour.
- Crypto trades every day, including weekends. Forex/gold (SMC accounts) only trade Sun 21:00 – Fri 21:00 UTC.
- The 3 new demo accounts have **$0**, so every trade on them is skipped until they are funded.

## 2. How it works
1. **Market data** – the MT5 bridge sends candles (daily, 4H, 15M).
2. **Strategy signal** – trend: EMA20 crosses EMA100 (daily). SMC: A+ setup (+ AI check on `smc_ai`).
3. **Risk check** – lot sized from each account's balance and risk %; skipped if over the cap.
4. **Order** – sent to every account assigned to that method, with a hard broker-side stop loss.
5. **Manage** – trend: stop raised daily, exit when the trend ends. SMC: TP1 partial + breakeven, TP2. Extra accounts are checked every 5 min.
6. **Telegram + log** – opened / skipped / rejected / closed alerts; everything saved in `trades.db`.

The loop runs every 15 s. Trend check hourly at :05. SMC scan every 15 min in kill zones (00–04, 07–10, 12–15 UTC) and at each 4H close.
On a slow AI or bridge the bot retries instead of dropping a trade, and never sends the same order twice.

## 3. Methods and accounts
| Method | Rules | Markets | Trades |
|---|---|---|---|
| `trend` | Buy when daily EMA20 crosses above EMA100. Stop 3×ATR(20), raised daily. Exit when EMA20 closes below EMA100. Long only, no fixed target. | SOL, XRP, ADA, DOGE, BNB, BTC, ETH (BNB/BTC/ETH skipped on ~$20 accounts: min lot too big) | ~9/year total |
| `trend_fast` | Same rules with EMA15/80 (7-coin backtest PF 2.31, both halves profitable; deeper drawdowns). | Same 7 coins | ~13/year total |
| `smc` | Owner's original ICT/SMC A+ setups, mechanical. | Best 5: BTCUSD, EURJPY, GBPUSD, GBPJPY, ETHUSD (gold removed) | 3-4/week |
| `smc_ai` | Same, but Gemini must approve (and can upgrade B+). If the AI is unreachable, A+ trades anyway. | same | fewer |

| Account | Server | Method | Risk | Balance |
|---|---|---|---|---|
| 436015341 (main) | Exness-MT5Trial9 | trend | 10% | $24.47 |
| 477674441 | Exness-MT5Trial9 | smc | 10% | $20 |
| 486074829 | Exness-MT5Trial10 | smc_ai | 10% | $20 |
| 486074840 | Exness-MT5Trial10 | trend_fast | 10% | $20 |

The main account must stay logged in on the bridge. The bot switches into the other accounts only to trade or manage them, then switches back.

## 4. What went wrong before, and the fixes
Original bot: 12 trades Oct 18–30, 2 real wins, about −$15.60 net; then 0 trades Oct 31 – Nov 3.
| Problem | Effect | Fix |
|---|---|---|
| EMA-invalidation exit | closed 5 trades 2–14 min after entry | removed |
| AI fallback auto-approved at "75%" | unchecked trades | AI never blocks or rubber-stamps; retries once |
| Gold 0.01 lot, $93 stop on ~$40 | −$17.32 | trades over the risk cap are skipped |
| 3.5–8 pip stops on 15M | noise stop-outs | per-asset stop buffer |
| `strategy.py` missing `import config` | 151 scans crashed | fixed |
| AI JSON truncated + dedup | all setups since Oct 31 rejected | JSON mode; dedup only after a decision |
| Bridge hiccup → trade logged closed at $0 | +$4.41 win logged as $0 | never close without MT5 history; record fixed |
| 12 s bridge timeout | filled order could be lost | 60 s, adopts filled position, no double send |
| TTL / TP1 retried every 15 s | 1,455 / 313 failed attempts | attempt limits; min-lot TP1 → breakeven only |
| Kill-zone limit counted whole day | blocked later sessions | counted per kill zone |
| 7.5% risk + forced $1.50 minimum | oversized risk | per-account risk; skips reported |

## 5. Research results (profit factor: >1.0 = profitable; older = first 60% of trades, recent = last 40%)
| Method | Trades | PF all | PF older | PF recent | Verdict |
|---|---|---|---|---|---|
| Crypto trend long-only, 4 coins | 37 | 5.18 | 2.30 | 10.67 | Pass |
| Crypto trend long-only, 7 coins (live since 2026-11-03) | 73 | 3.41 | 1.59 | 6.67 | Pass |
| Crypto trend, 7 coins, EMA15/80 (faster) | 104 | 2.31 | 1.33 | 4.09 | Pass, not live (bigger drawdowns) |
| Original SMC (forex, ~4 yr) | 379 | 0.71 | 0.98 | 0.44 | Fail |
| Trend on forex, long only | 120 | 0.57 | 0.55 | 0.60 | Fail |
| Trend on forex, long+short | 228 | 0.52 | 0.59 | 0.39 | Fail |
| Trend on gold+silver | 12 | 2.34 | 4.39 | 0.58 | Fail (too few, recent lost) |
| London breakout 1R | 8,729 | 1.00 | 1.01 | 1.00 | Fail (break-even) |
| London breakout 2R | 8,729 | 1.04 | 1.05 | 1.04 | Fail (gone after slippage) |

Crypto trend held for all 12 nearby settings. A 4H version failed. SOL gave much of the profit; only one signal since Jan 2025.
Forex can't run on $20: one 3×ATR stop at the 0.01 minimum lot ≈ $15 on EURUSD (75% of $20), ≈ $190 on gold.

### Real account history (MT5, account 436015341, May–Oct 2026, 89 closed trades)
19 wins (avg +$14.12, biggest +$73.44), 70 losses (avg −$7.96, biggest −$90.99), net −$288.88.
| Symbol | Trades | Net |
|---|---|---|
| XAUUSD (gold) | 12 | −$270.44 (11 losses, all 5 margin stop-outs) |
| BTCUSD | 20 | +$29.03 |
| EURJPY | 3 | +$14.42 |
| All forex | 47 | −$43.62 |
| Everything except gold | 77 | −$18.44 |
→ Gold removed from the SMC scanner (2026-11-03). Add it back only on a $1,000+ account.
→ SMC now trades only the best 5: BTCUSD, EURJPY, GBPUSD, GBPJPY, ETHUSD (each profitable in real history or the backtest).
A stop loss limits each loss to the risk % of the balance (10% of $20 = $2; 10% of $500 = $50), not a fixed dollar amount.

## 6. Risk vs. result (crypto trend, $20 start, 2019–2026)
| Risk/trade | $20 became | Deepest fall | Lowest balance | Time to double |
|---|---|---|---|---|
| 2% | $68 | 14% | $18.36 | 3.5 yr or never |
| 5% | $139 | 25% | $14.98 | 6–24 months |
| 8% | $298 | 38% | $12.57 | 6–24 months |
| 10% (live) | $478 | 46% | $12.08 | 5–24 months |
About 60% of trades lose. More risk does not add trades. On $20, minimum lots push real risk to 9–18%.

### What fits a $20 account (20% cap, checked 2026-11-03 with real stop sizes)
| Trade type | Fits on $20? |
|---|---|
| SMC 4H setups (GBPUSD, EURJPY, GBPJPY, BTC) | No: one stop costs $6–29 (30–140%) at the minimum lot, so they are skipped |
| SMC 15M kill-zone setups (GBPUSD, EURJPY, GBPJPY, ETH) | Yes: 9.5–17.5% risk |
| SMC 15M BTC | No |
| Trend SOL, XRP, ADA, DOGE | Yes: 9–18% risk |
| Trend BNB, BTC, ETH | No |
Fix 2026-11-03: the cap used to round the lot UP, which let a 30% risk through the 20% cap. It now rounds down.
To let SMC 4H setups trade, an account needs roughly $50–150 (BTC needs ~$150), or a higher MAX_RISK_PCT.
→ Owner chose MAX_RISK_PCT = 35% (2026-11-03): SMC 4H setups now trade on $20 at ~30–35% risk per trade (one loss ≈ $6–7).

## 7. Telegram commands
Every trade message starts with the bot that took it: **📈 Crypto Trend · #login**, **⚡ Crypto Trend Fast · #login**, **🎯 SMC (yours) · #login** or **🧠 SMC + AI · #login**
(opened, skipped, rejected, TP1, closed with ✅ WIN / ❌ LOSS). SMC scan summaries are labelled **🎯 SMC scanner**.
**🏆 Scoreboard** button / `/scoreboard`: refreshes every account's balance and ranks the bots by closed-trade P&L, win rate, open trades.
`/start` menu · `/status` balance · `/report` P&L · `/accounts` list · `/connect` add account ·
`/strategy [login] [trend|trend_fast|smc|smc_ai]` · `/risk [login] [pct]` · `/pause [login]` · `/resume [login]` · `/remove [login]` ·
`/news` · `/ai [pair]` · `/pulse` · any other text = AI chat.

## 8. Operations (VPS: `ssh trading-bot`)
| Task | Command |
|---|---|
| Check services | `systemctl is-active xvfb mt5-terminal mt5-bridge fxalexg-bot` |
| Live log | `sudo journalctl -u fxalexg-bot -f` |
| Bridge log | `sudo journalctl -u mt5-bridge -n 100` |
| Restart bot | `sudo systemctl restart fxalexg-bot` |
| Roll back | `cp -a /home/ubuntu/fxalexg_bot_backup_20261103/. /home/ubuntu/fxalexg_bot/` then restart |
| Research | `cd /home/ubuntu/research && python3 trend_research.py data` |

Key files: `bot.py` (loop, dispatch, Telegram), `trend_strategy.py`, `strategy.py` (SMC), `risk_manager.py`,
`bridge_client.py` (retries + order recovery), `config.py` (settings), `accounts.db`, `trades.db`,
`walkforward_backtest.py` / `trend_research.py` / `london_research.py` (backtests).
Rules for future changes: test any strategy change with the backtests first (older AND recent data must pass);
don't run big data downloads while trading (the bridge does one request at a time);
don't edit files with PowerShell `Get-Content`/`Set-Content` (it corrupted the emoji once).

## 9. Open items
- [x] Fund demo accounts 477674441, 486074829, 486074840 ($20 each, 2026-11-03)
- [x] Same risk (10%) on all accounts (done 2026-11-03)
- [ ] Before a real $20 account: confirm SOLUSDm, XRPUSDm, ADAUSDm, DOGEUSDm exist with the same minimum lots
- [ ] Change the demo passwords (shared in chat; stored lightly scrambled)
- [ ] Review results after a few weeks and pick the winner
