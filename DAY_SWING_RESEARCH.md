# Day trading vs. day + swing: what the bot does and why

Question: can the bot also be a **day trader** (in and out the same day) as well as a swing trader?
Short answer: **pure day trading lost money in every test; "day entries + swing exits" makes money**, so the
bot now trades that way on crypto and gold. Script: `day_research.py` (re-runs every number below).

## 1. Pure day trading (every trade closed by the end of the day): does not pass
Five families, both directions, gold + 11 forex pairs (1H, 2012–2019 design / 2020–2025 later years) and
10 coins (1H, 2022–2023 design / 2024–2026 later years), ~150 settings, costs included:

| Family | Gold PF | Forex PF | Crypto PF |
|---|---|---|---|
| Prior-day high/low breakout in the daily trend | 0.84–1.13 | 0.69–0.89 | 0.70–1.03 |
| Opening-range breakout (1–2h range, range or ATR stop, 1R/2R/day's end) | 0.75–1.01 | 0.76–0.87 | 0.76–0.94 |
| Intraday momentum (morning move → afternoon) | 0.78–1.13 | 0.66–0.88 | 0.82–1.02 |
| RSI(2) pullback in the daily trend | 0.68–0.91 | 0.76–0.79 | 0.78–0.96 |
| 12/24-candle channel breakout, flat at day's end | 0.88–1.26 | 0.82–0.89 | 0.76–0.97 |

Only one setting was above 1 in both periods — gold, 12-candle channel, PF 1.03 / 1.01: break-even, not an
edge. With **zero** costs the best ones only reach PF 0.9–1.2 in the later years: the intraday edge is smaller
than the spread.

## 2. Why: the profit is in the trades that last more than a day
The live gold 1H rule (GOLD_FOREX_RESEARCH.md), 2012–2025:

| Trades that… | Share | Total result |
|---|---|---|
| closed within 24 hours | 76% | −137 R (small, quick losses) |
| lasted 1–3 days | 20% | +170 R |
| lasted over 3 days | 4% | +80 R |

Forcing every trade shut by the end of the day cuts off exactly the winners that pay for everything.

## 3. What the bot does instead: day entries, swing exits
Every engine decides on the **live candles every hour**; failed trades are cut quickly (mostly the same day),
winners are trailed for days. New this round — a **crypto 1H engine** (`mom_1h` in momentum_strategy.py):
1H candle closes above the highest high of the last 168 1H candles (one week), only while BTC is above its
100-day EMA; stop 3×ATR(20) on 1H (~3% of price), trailed every hour; risk capped at **2%** per trade.

| 10 coins | Trades | Per week | Win rate | PF |
|---|---|---|---|---|
| 2022–2023 (bear market, design) | 318 | ~3 | 36% | **1.30** |
| 2024–2026 (later years) | 749 | ~7.5 | 40% | **1.40** |

- All 10 coins profitable in 2024–26 (PF 1.03–2.26); nearby settings (120–168 candles, 2.5–3 ATR) PF 1.28–1.40.
- Entering one candle late (hourly runs): PF 1.34 / 1.31. Extra 0.2% cost per trade: PF 1.13 / 1.20.
- 60% of its trades end within a day (−224 R), the 40% that run 1–3+ days make +347 R.
- Account simulation (max 4 open trades): 2% risk → +39%/yr (worst drawdown 34%) in 2022–23, +52%/yr
  (35%) in 2024–26. At 5% the drawdowns reached 68–70% and returns fell, hence the 2% cap.
- Crypto **sells** on the same 1H rule: PF ~1.0 — no edge, so crypto stays buy-only. Gold trades both ways.

## 4. Candle data check (every run)
Each run now verifies every candle set it reads (1H/4H/daily per symbol): enough candles, and the newest
candle not behind the market's own last price. A freshly installed terminal that is still downloading history
is retried; anything still incomplete is skipped, logged, shown in `/health`, and reported once a day.
