# Momentum Breakout: research behind the "momentum" method (2026-11-24)

## Goal
Find, from what profitable bots and published research actually use, a strategy that keeps making money
on coins and years it was never tuned on, and that a ~$10 MT5 account can size. Then run it in the bot.

## What the research says (sources at the end)
| Approach | Evidence | Verdict |
|---|---|---|
| Trend following / time-series momentum | Strongest academic support in crypto (Liu & Tsyvinski; "A Decade of Evidence of Trend Following in Cryptocurrencies"); Concretum's Donchian-breakout ensemble on the top 20 coins reports Sharpe 1.57, max DD 11% | Test |
| Cross-sectional momentum (rotate into the strongest coins) | Mixed: weaker than time-series, crash-prone (−255% month in Dec 2020) | Test |
| Grid bots (Pionex style) | Profit only in sideways markets; lose against trends; no independent study | Test |
| DCA bots (3Commas style) | Lower average entry, but amplify losses in long bear markets | Test |
| Machine-learning "AI" bots | Naive ML signals stop working once ~0.1% costs are charged (Bysik & Ślepaczuk 2026) | Test |
| Forex EAs on Myfxbook | Verified-badge accounts still hide resets and risk; no independent success rate | Not testable |
| Dip-buying (RSI mean reversion) | Mixed; works mainly in bull regimes | Test |

## Data and method
- Daily candles for 94 coins, **Dec 2017 – Nov 23 2026** (Binance, via public GitHub mirrors: priyanshux/cryptopy,
  marek3993/trendatlas-crypto, Shenhan01-sys/Fabius). Covers the 2018, 2022 and 2025-26 bear markets.
- 4H candles for 42 coins, Mar 2024 – Oct 2026 (SandPearlStone/trading-bot, MoeAZack/ZackBot2).
- Rules and settings were chosen ONLY on 8 large coins before 2023. Everything else (the same coins 2023-2026,
  and every other coin) is out-of-sample.
- Costs: spread 0.03–0.3% per trade, swap 15%/yr. Fills at the next candle's open. Bars where stop and
  target are both touched are scored against the bot.

## Results
PF = profit factor (above 1.0 makes money). R = multiples of the amount risked per trade.

| Strategy | Design coins | Same coins 2023+ | Unseen coins | Verdict |
|---|---|---|---|---|
| **Daily breakout of the 20-day high, 3×ATR trailing stop, only while BTC > EMA100** | PF 5.42 | **1.87** | **2.16** (79 coins, 1,405 trades) | **Pass** |
| Same without the BTC filter | 4.04 | 1.87 | 1.86 | Pass, fewer good years |
| All 60 nearby daily settings (7–55 day breakout, 2–4 ATR, 4 filters) | 1.97–8.06 | 1.28–2.30 | 1.17–2.53 | Every one profitable |
| **4H breakout of the 42-candle high, 2.5×ATR trailing stop, BTC > EMA100** | 1.62 | – | **1.33** (34 coins, 1,040 trades) | **Pass** |
| Same 4H rule without the BTC filter | 1.55 | – | 1.12 (first half 0.95) | Weak |
| Dip-buying (RSI(2) < 10 in uptrends) | 2.66 | 1.54 | 1.49 | Works, but stops ~19% (can't size on $10) |
| Weekly rotation into the top 3–10 coins | Sharpe 1.1–1.2, ~100%/yr | | | Rejected: 60–68% drawdowns |
| DCA bot (5 safety orders, TP +1.5%), conservative fills | | | 5 of 8 coins lose (2024-26); ETH deal hit −101% (2018-26) | Rejected |
| Grid bot (±15%, 20 levels), conservative fills | | | −14% average (2024-26), far worse through crashes | Rejected |
| Gradient-boosting ML, walk-forward yearly | beat buy-and-hold 6 of 7 years at prob > 0.65 | | lost in 2022, no stop loss | Not used: the edge is mostly momentum, already captured more simply |
| Breakdown shorts while BTC < EMA100 | 1.18–1.42 | 0.53–0.67 | 0.53–0.94 | Rejected: fails out of sample |

Note on grid and DCA: with "best case" fills inside a candle they looked very profitable (+58% / +132%).
Scored conservatively they lose. Most bot sales pages show the first kind of backtest.

## The strategy (`momentum_strategy.py`, account method `momentum`)
1. **Market filter**: trade only while Bitcoin's daily close is above its 100-day EMA.
   When it turns off: no new buys, and daily-engine trades are closed.
2. **4H engine**: buy when a 4H candle closes above the highest high of the previous 42 candles (last week's high).
   Stop 2.5 × ATR(20) (~4–5% of price), raised after every 4H close to (highest close − 2.5 × ATR). Never lowered.
3. **Daily engine**: buy when a daily candle closes above the previous 20 days' high. Stop 3 × ATR(20) (~14%),
   raised daily. Trades only when the minimum lot fits the risk cap (needs roughly $100+).
4. Long only, no fixed target, one trade per coin, max 4 open, skip if spread > 15% of the stop,
   never risk more than `MOMENTUM_MAX_RISK_PCT` (8%) on one trade even at the minimum lot.
5. Coins: SOL, XRP, DOGE, ADA, BNB, ETH, BTC (`MOMENTUM_SYMBOLS`). Coins the account can't size are skipped automatically.

## What to expect
**$10 account** (only SOL/XRP/DOGE/ADA can be sized; 4H engine), Apr 2024 – Oct 2026:
| Risk per trade | Max open | $10 became | Lowest point | Trades |
|---|---|---|---|---|
| 3% | 4 | $30.37 | $7.07 | 138 (~1.1/week) |
| 5% | 4 | $47.67 | $5.54 | 138 (~1.1/week) |

By 6-month window at 5%: $6.42, $26.63, $17.90, $9.12, $12.66. Profits come in bursts, so some 6-month stretches lose.

**Larger account, daily engine, $100 at 5% risk, one year from each Jan 1:**
2019 $155 · 2020 $527 · 2021 $531 · 2022 $92 · 2023 $161 · 2024 $130 · 2025 $79 · 2026 $107 (to Nov).

**Right now (Nov 24 2026) the market filter is OFF**: BTC closed at 61,271, 17.8% below its EMA100 (74,527),
for 19 days. The bot will wait in cash until BTC closes back above the EMA. Past OFF stretches lasted a median
of 5 days, the longest 212 days (2022). The waiting limits bear-market damage, it doesn't remove it:
in 2022 the daily rules lost −74.7R across all coins without the filter (204 trades) and −34.1R with it (63 trades).

## Limits
- 4H history is only 2.5 years; the daily engine carries the long-run evidence.
- Exness may not list every coin, or may charge different spreads/swaps than assumed; the bot checks the live
  spread against the stop before every trade.
- A backtest is not a promise. Run `python3 momentum_research.py --d1 DIR.. --h4 DIR..` again on fresh bridge
  data every few months (`/ohlcv/SYMBOL/D1/3000` and `/ohlcv/SYMBOL/H4/5000` saved as SYMBOL_D1.json / SYMBOL_H4.json).

## How to use
`/strategy <login> momentum` then `/risk <login> 5` (or 3 for smaller swings). Tests: `python3 test_momentum.py`.

## Sources
- Liu & Tsyvinski, Risks and Returns of Cryptocurrency — https://www.nber.org/system/files/working_papers/w24877/w24877.pdf
- Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market — https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf
- Cryptocurrency momentum has (not) its moments — https://link.springer.com/article/10.1007/s11408-025-00474-9
- Zarattini, Pagani, Barbon, Catching Crypto Trends — https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/
- A Decade of Evidence of Trend Following Investing in Cryptocurrencies — https://arxiv.org/abs/2009.12155
- Machine Learning-Based Bitcoin Trading Under Transaction Costs — https://arxiv.org/abs/2606.00060
- Freqtrade out-of-sample collapse example — https://backtestsnotsignals.substack.com/p/freqtrade-strategy-983-in-6-years
- Grid bot profitability — https://www.freyafinance.com/academy/strategy-guides/grid-bot-profitability-real-market-analysis
- Reading a Myfxbook record — https://www.mql5.com/en/blogs/post/779425
- RSI mean reversion on Bitcoin — https://www.quantifiedstrategies.com/bitcoin-rsi-trading-strategy/
