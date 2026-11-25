# Gold + Forex research: strategies that trade BOTH directions

Question: can the bot also make money while crypto is falling, with buy **and** sell trades on gold or forex?
Answer: **gold yes, forex no.** The bot now trades gold 1H breakouts both ways (`metals_strategy.py`).
Forex pairs were tested the same way and none of the rules survived trading costs, so the bot does not trade them.

Script: `gold_research.py` (re-runs every number below). Live rule: `metals_strategy.py` — the research
script calls its signal functions, so the backtest is the live code.

## Data and method
| Source (free, public) | Instruments | Period |
|---|---|---|
| github.com/ejtraderLabs/historical-data | 11 forex pairs + gold, daily/4H/1H | Nov 2012 – Mar 2022 |
| github.com/ts4blader/market_data | EURUSD GBPUSD AUDUSD USDCAD USDCHF + gold, daily/4H/1H | 2020 – 2025 |
| github.com/13otKmdr/MT5-AlgoLab | 28 forex pairs + gold + silver, 4H | Jan 2024 – Jan 2026 |

- **Design** = 2012–2019 (11 pairs + gold). **Later years** = 2020–2025, never used to choose anything.
  **Unseen** = 16 other pairs + silver (2024–2026), never used in design.
- Costs: Exness Standard spreads (EURUSD 1.2 pips … crosses 3 pips, gold $0.30), overnight swap 2%/yr of the
  position (4% on metals), fills at the next candle's open, gaps through a stop fill at the open, and if a
  candle touches both stop and target the stop is assumed first.
- Every rule trades long **and** short. PF = profit factor (money won ÷ money lost; above 1 = profitable).

## Forex: nothing passes
| Family (both directions, ~100 settings) | Design PF | Later-years PF | Unseen PF |
|---|---|---|---|
| Daily channel breakout (20/55 days, 2–3 ATR trail, with/without trend filter) | 0.72–0.85 | 0.72–0.93 | 0.49–0.71 |
| 4H channel breakout (42/84 candles) | 0.89–1.03 | 0.84–0.94 | 0.66–0.81 |
| Moving-average crosses (10/50, 20/100, 50/200; daily and 4H) | 0.70–1.03 | 0.49–0.92 | 0.33–0.87 |
| RSI(2) mean reversion (daily, 4H) | 0.81–0.96 | 0.79–1.08 | 0.70–1.68 (unstable) |
| Bollinger-band fade (daily, 4H, ADX range filter) | 0.73–0.88 | 0.79–1.04 | 0.71–1.19 |
| London-open breakout (1H, Asian box, 1R/2R/end of day) | 0.83–0.95 | 0.86–0.94 | – |

With zero costs the same rules score PF 0.90–1.20: forex has very little trend or reversal edge left, and the
spread and swap take all of it. Pair by pair, the few pairs that worked in 2012–2019 (GBPUSD, GBPJPY, USDCAD)
failed in 2020–2025, and 14 of 16 unseen pairs lost. The two exceptions in the unseen group were CHFJPY and
**silver** — a metal.

## Gold: the trend works both ways
Gold breakouts were profitable on every timeframe and almost every setting, in both periods, once trades were
limited to the direction of gold's daily trend (buys above the 200-day EMA, sells below).

**Live rule** — 1H candle closes beyond the last 120 candles' high/low (≈ 5 trading days), stop 2×ATR(20),
trailed every hour, no target:

| Period | Trades | Per week | Win rate | PF | Buys PF | Sells PF |
|---|---|---|---|---|---|---|
| 2012–2019 (design) | 339 | 0.9 | 38% | **1.44** | 1.43 | 1.45 |
| 2020–2025 (later years) | 350 | 1.1 | 37% | **1.31** | 1.30 | 1.40 |

Robustness:
- Every nearby setting is profitable: lookback 72/120/168 candles, stop 1.5–2×ATR, trail 2–4×ATR,
  EMA 100 or 200 → PF 1.21–1.56 (design) and 1.20–1.40 (later years).
- Entering one or two candles late (the bot runs hourly): PF 1.37 / 1.17 and 1.42 / 1.35.
- Costs: still profitable at about 3× the normal spread at today's gold price.
- Silver (never used in design), same 5-day breakout on 4H, 2024–2026: PF 1.35–1.49.
- Gold 4H / daily versions are also profitable (PF 1.2–3.4) but have stops too wide for small accounts, and
  combining them with the 1H rule did not improve results, so only the 1H rule is live.

Growth with exact position sizing (compounded, costs included):

| Risk per trade | 2012–2019 | worst drawdown | 2020–2025 | worst drawdown |
|---|---|---|---|---|
| 1% | +9%/yr | 15% | +8%/yr | 23% |
| **2% (default)** | **+17%/yr** | **28%** | **+14%/yr** | **43%** |
| 3% | +24%/yr | 40% | +20%/yr | 58% |

It wins about 1 trade in 3; the winners are much larger than the losers. Losing streaks of 8–12 trades happen.

## Account size: important
On an Exness **Standard** account the smallest gold trade is 0.01 lot = 1 ounce. With gold around $3,567 a
typical stop is $16–18 away, so the smallest trade risks **$16–18 — about 7–8% of a $224 account**. The bot's
gold cap is 5%, so on that balance it skips gold signals (and tells you once per signal).

Tighter stops don't fix this. Replaying 2020–2025 at today's gold price on a $224 Standard account trading
the 1-ounce minimum, every stop size (1–2 × ATR) ended between −20% and +31% a year with worst drawdowns of
60–86%. The rule's edge survives tighter stops (PF 1.30–1.61), but 1-ounce steps are far too coarse for $224.

**The fix the bot supports: a separate Standard Cent account for gold.** On a cent account the balance is
counted in US cents, so the same 1-ounce trade risks cents and the bot can size exactly 2%.
1. Exness Personal Area → *Open new account* → **Standard Cent**, MT5, demo or real. Note the account number,
   trading password and server.
2. In Telegram: `/goldlogin ACCOUNT PASSWORD SERVER` (the message is deleted, the login stored encrypted).
3. Every hourly run then trades **gold on the cent account** (`XAUUSDc`, found automatically) and **crypto on
   your main account**. Gold messages start with "🥇 Gold account". Cent amounts are shown in dollars.
   `/goldlogout` moves gold back to the main account.

Other options: a balance of about $350+ (5% cap) or $850+ (true 2%), or `/goldrisk 8` to allow the 1-ounce
trade at ~7–8% risk (not recommended: the drawdowns above).
