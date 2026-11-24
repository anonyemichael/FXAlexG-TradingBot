# FXAlexG strategy research: more trades per week on a ~$10 account (2026-11-24)

## Question
The live `trend` method takes about 9 trades a year, so weeks pass without a trade. Can Alex G's
(FXAlexG, YouTube @fxalexg__) "Set & Forget" swing method give several trades a week, profitably, on $10?

## What could be studied
- The session's network blocks youtube.com, tradingview.com, scribd.com and fxalexg.com, so **no video or
  transcript was watched directly**. His rules come from web-search summaries of his videos, guides and
  community checklists (TradingView "Set & Forget – AlexG Club Checklist", Swing Trading Lab material).
- His method as consistently described:
  1. **Trend** on Weekly, Daily and 4H from swing structure, marked with candle **bodies** (closes);
     trade only when at least 2 timeframes agree.
  2. **Area of interest (AOI)**: a zone price has turned from several times (support/resistance flip),
     with extra confluence from the 50 EMA and round numbers.
  3. **Entry**: at the AOI, wait for a **shift of structure** plus an **engulfing** candle.
     Stop beyond the swing, target at least **1:2.5**. Set the order and don't touch it.
- His claimed 60–65% win rate could not be verified.

## Data (free, public, 4H candles)
| Set | Instruments | Period |
|---|---|---|
| MT5 broker export (github 13otKmdr/MT5-AlgoLab) | 28 forex pairs + gold + silver | Jan 2024 – Jan 2026 |
| ts4blader/market_data | EURUSD GBPUSD AUDUSD USDCAD USDCHF XAUUSD US100 US500 (+1H) | 2020 – 2025 |
| Binance (SandPearlStone/trading-bot + MoeAZack/ZackBot2) | 8 large coins | Mar 2024 – Oct 2026 |
| same sources, out-of-sample | 34 other coins | Sep 2024 – Oct 2026 |

Costs: typical Exness spreads (majors ~1 pip, crosses 1.5–3.5, gold $0.25, crypto 0.03–0.3%), plus 15%/yr swap on crypto.
Fills at the next candle's open; if stop and target fall in the same candle the stop is assumed.

## Results
PF = profit factor (above 1.0 makes money; below 1.0 loses). R = multiples of the risk per trade.

### 1. Alex G Set & Forget, coded literally (`alexg_strategy.py`, `alexg_research.py`)
About 40 variants: trend by structure or 50 EMA, 2 or 3 timeframes in sync, AOI strict/loose/flip-only,
engulfing loose/strict, RR 1.5–4, wider stops, 4H / 1H / daily entries.

| Version | Trades/week | Win % | PF | Verdict |
|---|---|---|---|---|
| As described (2-of-3 sync), 28 pairs | 15.2 | 27% | 0.90 | Loses |
| Best: W1+D1+4H all in sync, 28 pairs | 6.9 | 30% | 1.06 | ~Break-even, drawdown 55R |
| Same, 2020–2025, 8 symbols | 1.9 | 31% | 1.09 | ~Break-even |
| Same, 8 crypto coins | 2.7 | 29% | 0.98 | Loses |
| 1H "day trading" entries | 7–15 | 28% | 0.95–1.01 | Break-even at best |
| Daily entries | 0.6–3 | 31–37% | 0.93–1.57 | Profit only on gold/US100/US500 (strong one-way trends); forex pairs break even |

Removing the AOI condition barely changed results, so the mechanical AOI adds nothing. Any edge Alex has
comes from his own judgment, which can't be coded. **Not recommended for real money.**
Available as account method `alexg` for demo testing (forex/gold need ~$150+ for the 0.01 lot).

### 2. 4H crypto break of structure + trailing stop (`swing_strategy.py`, `swing_research.py`) — the one that passed
Buy when a 4H candle closes above the previous 42 candles' high (last week's high), stop 2×ATR(20),
trailed every 4H (highest close − 2×ATR), no fixed target, long only.

| Test | Trades/week | Win % | PF | 1st / 2nd half |
|---|---|---|---|---|
| 8 large coins | 3.8 | 38% | 1.55 | 1.25 / 1.84 |
| same, triple spreads | 3.8 | 35% | 1.28 | 1.07 / 1.49 |
| SOL/XRP/ADA/DOGE (fits $10) | 1.7 | 44% | 2.00 | 1.80 / 2.19 |
| **34 coins never used for tuning** | 13.4 | 34% | **1.14** | **0.97** / 1.30 |
| 16 nearby settings (20–60 candle break, 2–3.5 ATR) | 2.8–7.0 | 32–39% | 1.17–1.48 | all >1 overall |
| Alex's fixed 1:2.5 target on the same entries | 1.6 | 36% | 1.30 | worse than trailing |
| Shorts (mirror) | 2.5 | 36% | 0.99 | no edge, not used |

Profits come in bursts (2024 Q4, 2025 Q3, 2026 Q3); most other quarters are flat or slightly negative.
The weak out-of-sample result means this is **promising, not proven**.

### $10 account, SOL/XRP/ADA/DOGE, 26 weeks, max 3 open (real historical sequences)
| Start | 5% risk | lowest | 10% risk | lowest |
|---|---|---|---|---|
| 2024-06 | $31.71 | $8.47 | $64.38 | $7.08 |
| 2024-10 | $33.90 | $8.80 | $75.27 | $7.65 |
| 2025-01 | $11.60 | $10.00 | $10.79 | $10.00 |
| 2025-04 | $19.69 | $8.40 | $28.20 | $6.04 |
| 2025-07 | $17.03 | $8.96 | $23.75 | $7.98 |
| 2025-10 | $10.64 | $7.88 | $9.37 | $6.05 |
| 2026-01 | $10.56 | $8.41 | $8.60 | $5.62 |
| 2026-04 | $15.81 | $6.14 | $12.41 | $3.55 |

Using the weaker 34-coin results instead: at 5% risk, 54% chance of being below $10 after 26 weeks;
at 10%, 49% chance of halving the account. **5% risk is the better choice.**

## Why $10 can't trade forex or gold here
On MT5 Standard the minimum is 0.01 lot. A 4H Set & Forget stop (~0.5% of price) at 0.01 lot costs about
$5 on EURUSD (50% of $10) and ~$20 on gold. The risk manager rejects those trades. Exness Cent accounts
would fix the lot size but run on MT4, not MT5, so this bot can't use them. Small crypto coins are the only
instruments a $10 MT5 account can size properly.

## How to use
- Put the new method on a **demo** account first: `/strategy <login> swing4h` then `/risk <login> 5`.
- Leave the main account on `trend` (7-year evidence, PF 3.41). Both can run side by side on different accounts.
- `alexg` exists for anyone who wants to watch the literal Set & Forget rules on demo: `/strategy <login> alexg`.
- Re-run the research: download the CSVs above into folders named `SYMBOL_H4.csv`, then
  `python3 swing_research.py DIR [DIR..]` / `python3 alexg_research.py DIR --per-symbol`.
  Bridge JSON dumps (`SYMBOL_H4.json` from `/ohlcv/SYMBOL/H4/5000`) work too, giving longer Exness history.
- Offline checks: `python3 test_swing_alexg.py`.
