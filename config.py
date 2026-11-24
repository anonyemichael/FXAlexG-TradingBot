import os
from pathlib import Path

# --- Secure Environment Loader ---
def _load_dotenv():
    """Load .env key-value pairs into os.environ if present without requiring external libraries."""
    search_paths = [
        Path(__file__).resolve().parent / ".env",
        Path.cwd() / ".env"
    ]
    for p in search_paths:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            break

def _safe_int(val, default=0):
    try:
        return int(val) if val else default
    except (ValueError, TypeError):
        return default

_load_dotenv()

# --- Credentials & Endpoints (Loaded securely from environment / .env) ---
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or None  # Auto-discovered via /start or loaded from chat_id.json

# MT5 Bridge URL
BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:5001")

# --- MT5 Account Profiles ---
ACCOUNTS = {
    "demo": {
        "login":    _safe_int(os.getenv("MT5_DEMO_LOGIN", "0")),
        "password": os.getenv("MT5_DEMO_PASSWORD", ""),               # Uses saved session in MT5 terminal if blank
        "server":   os.getenv("MT5_DEMO_SERVER", ""),
        "label":    "Demo (Trial)",
    },
    "real": {
        "login":    _safe_int(os.getenv("MT5_REAL_LOGIN", "0")),
        "password": os.getenv("MT5_REAL_PASSWORD", ""),
        "server":   os.getenv("MT5_REAL_SERVER", ""),
        "label":    "Real (Live)",
    },
}
ACTIVE_ACCOUNT = os.getenv("ACTIVE_ACCOUNT", "demo")  # Default fallback; overwritten by active_account.json

# --- Strategy selection ---
# "trend": daily EMA20/100 trend-following, long-only, 3xATR trailing stop (trend_strategy.py).
#          Walk-forward tested 2019–2026: profitable in both halves and across all nearby settings;
#          $20 @ 2% risk -> ~$68, max drawdown ~14% (trend_research.py).
# "smc"  : the original ICT/SMC scanner — backtested NEGATIVE (profit factor 0.6–0.9). Not recommended.
STRATEGY = "trend"
# Coins with a small enough minimum lot for a $20 account and spread < 1%.
# (LTC excluded: 3.4% spread. Forex/gold/BTC/ETH: minimum lot risks 17–300% of $20 per daily ATR.)
# BNB/BTC/ETH added 2026-11-03 for more trades (~9/yr instead of ~5; 7-coin backtest PF 3.41, both halves
# profitable). Their minimum lot is too big for ~$20, so small accounts skip them automatically.
TREND_SYMBOLS  = ["SOLUSDm", "XRPUSDm", "ADAUSDm", "DOGEUSDm", "BNBUSDm", "BTCUSDm", "ETHUSDm"]
TREND_MAX_OPEN = 4
TREND_EMA_FAST = 20
TREND_EMA_SLOW = 100
TREND_STOP_ATR = 3.0

# --- 4H crypto swing (swing_strategy.py) — account method "swing4h" ---
# Buy when a 4H candle closes above the previous 42 candles' high (a break of last week's high), stop 2xATR(20),
# trailed every 4H, no fixed target. Backtest Mar 2024 - Oct 2026 (swing_research.py): 7 coins below + LTC,
# ~3.8 trades/week, profit factor 1.55, both halves profitable. On 34 OTHER coins it only reached 1.14, so it
# is unproven: run it on a demo account first. On ~$10 only SOL/XRP/ADA/DOGE fit (BNB/BTC/ETH min lot too big),
# which is ~1.7 trades/week.
SWING_SYMBOLS   = ["SOLUSDm", "XRPUSDm", "ADAUSDm", "DOGEUSDm", "BNBUSDm", "BTCUSDm", "ETHUSDm"]
SWING_BREAK_BARS = 42
SWING_STOP_ATR  = 2.0
SWING_MAX_OPEN  = 3
SWING_MAX_SPREAD_FRAC = 0.15   # skip if the live spread is more than 15% of the stop distance

# --- FXAlexG Set & Forget, mechanical (alexg_strategy.py) — account method "alexg" ---
# W1+D1+4H structure in sync, pullback into a daily AOI, 4H shift of structure + engulfing close, stop beyond
# the pullback, target 1:2.5, no management. Backtest: ~7 trades/week on these 28 pairs + gold but profit
# factor only 1.06 (2024-26) / 1.09 (2020-25, 8 symbols) with drawdowns of ~55R: about break-even.
# Demo / research only. Forex and gold need roughly $150+ (0.01 lot); smaller accounts skip every signal.
ALEXG_SYMBOLS = [
    "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm", "USDCHFm", "NZDUSDm",
    "EURJPYm", "GBPJPYm", "AUDJPYm", "CADJPYm", "CHFJPYm",
    "EURGBPm", "EURAUDm", "EURCADm", "EURCHFm", "EURNZDm", "GBPAUDm", "GBPCADm", "GBPCHFm",
    "AUDCADm", "AUDCHFm", "AUDNZDm", "NZDCADm", "NZDCHFm", "CADCHFm", "XAUUSDm",
]
ALEXG_PARAMS   = {}    # overrides for alexg_strategy.DEFAULTS, e.g. {"rr": 3.0}
ALEXG_MAX_OPEN = 3
ALEXG_BARS     = 1600  # 4H candles fetched per symbol (~1 year: builds the W1/D1 trend and AOIs)

# Pairs to scan (SMC strategy only)
# XAUUSDm removed 2026-11-03: real MT5 history (May–Oct 2026) shows gold lost $270 of the $289 total
# (11 of 12 trades lost, all 5 margin stop-outs); everything else was about break-even. Its 0.01 minimum
# lot risks $20–90 per stop, far too big for small accounts. Add it back only on an account of $1,000+.
# Best 5 (owner's request, 2026-11-03): each was profitable in real MT5 history or in the 4-year backtest.
# BTC +$29 real, EURJPY +$14 real, GBPUSD best in backtest, GBPJPY positive in backtest, ETH ~break-even real.
# Dropped EURUSD/USDJPY/AUDUSD/USDCAD (lost in both) and SOL/XRP/LTC (no record with this method).
SYMBOLS = ["BTCUSDm", "EURJPYm", "GBPUSDm", "GBPJPYm", "ETHUSDm"]

# --- High-Growth Lot Size & Risk Engine ---
# Targets $3.50 - $5.00 risk per trade to yield $10.00+ on winning trades
USE_FIXED_LOT   = False
# AGGRESSIVE growth profile (owner's choice). Trend strategy backtest 2019–2026, $20 start:
#   2% -> $68 (maxDD 14%) | 5% -> $139 (25%) | 8% -> $298 (38%) | 10% -> $478 (46%, low point $12)
# Each trade still has a hard broker-side stop loss; the cap stops min-lot rounding from going wild.
RISK_PERCENT    = 10.0
FIXED_LOT       = 0.01   # Fallback lot size if calculation fails

MIN_RISK_USD    = 0.0    # No forced minimum
MAX_RISK_PCT    = 35.0   # Hard cap per trade. Owner chose 35% (2026-11-03) so SMC 4H setups fit $20 accounts
                         # (a 4H stop at the 0.01 minimum lot costs ~$6-7 = 30-35% of $20).
MAX_RISK_USD    = 1000.0 # Dollar cap high enough not to block compounding
MIN_BALANCE     = 5.0    # USD — bot pauses if balance falls below this

# Trade Frequency & Concurrency
MAX_TRADES_DAY       = 4
MAX_TRADES_KILL_ZONE = 2
MAX_CONCURRENT       = 2
NEWS_LOOKOUT_MINS    = 30 # Skip entry if high-impact news within 30 min

# --- Take Profit, Risk-Reward & Trailing Stop ---
MIN_RR            = 2.0   # Minimum RR ratio required to accept a setup
TP1_RR            = 1.5   # Take 50% partial profit at 1:1.5 RR and move SL to breakeven
TP2_RR            = 3.5   # Final Take Profit at 1:3.5 RR
PARTIAL_PCT       = 0.5   # Close 50% of position at TP1 (skipped automatically if lot is at broker minimum)
USE_TRAILING_STOP = True  # Trail SL behind 15M EMA21 after TP1
TRAILING_STOP_BUFFER = 15 # Pips buffer

# Trade Lifecycle
MAX_TRADE_LIFESPAN_HOURS = 36 # Close trade if TP1 not hit within 36h

# --- Quality & Safety Filters ---
ADX_PERIOD     = 14    # ADX calculation period
ADX_MIN        = 20    # Minimum ADX for trend confirmation — skip ranging markets

COOLDOWN_HOURS = 24    # Skip same symbol for 24h after a loss (prevents revenge trades)

# SL Lookback (number of 4H candles to scan for swing structure)
CRYPTO_SL_LOOKBACK = 12   # 48 hours — deeper structure for volatile crypto
FOREX_SL_LOOKBACK  = 6    # 24 hours — standard for forex pairs
CRYPTO_ATR_BUFFER  = 0.5  # ATR multiplier for crypto SL padding
FOREX_ATR_BUFFER   = 0.2  # ATR multiplier for forex SL padding

# Entry Quality Gates
MIN_GRADE       = "A+"   # Base mechanical threshold
MIN_TF_SYNC     = 2      # Allows shorts/longs when D1+4H agree even if W1 is neutral/counter
MIN_TF_SYNC_KZ  = 2      # Allow 2/3 sync for Kill Zone 15M CHoCH entries

# --- AI Intelligence Engine (100% Google Gemini Multi-Key High Availability Pool) ---
AI_ENABLED               = os.getenv("AI_ENABLED", "true").lower() in ("true", "1", "yes")
GEMINI_API_KEY           = os.getenv("GEMINI_API_KEY", "")
_raw_keys                = os.getenv("GEMINI_API_KEYS", "") or f"{os.getenv('GEMINI_API_KEY','')},{os.getenv('GEMINI_API_KEY_2','')},{os.getenv('GEMINI_API_KEY_3','')}"
GEMINI_API_KEYS          = [k.strip() for k in _raw_keys.split(",") if k.strip()]
GEMINI_MODEL             = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AI_TIMEOUT               = 45     # Extended to 45s for complete institutional analysis
AI_MAX_RETRIES           = 3      # Exponential backoff retries against transient rate-limits
AI_CONFIRMATION_REQUIRED = True   # AI evaluates all candidate setups before opening orders
AI_MIN_CONFIDENCE        = 70     # Minimum AI confidence (0-100) to approve trade
AI_UPGRADE_B_PLUS        = True   # AI can upgrade high-scoring B+ (80+) setups if confidence >= 85 (veto mode only)
# "advisory" = AI comment attached to alerts, mechanical rules decide (default)
# "veto"     = AI can block trades / upgrade B+ (old behaviour — blocks everything when Gemini is down)
# "off"      = no AI call during scans
AI_GATE_MODE             = "advisory"
AI_AUTO_TRAIN            = False  # Rewriting policy after every close learned from 12 trades + a mis-logged result
AI_POST_TRADE_REVIEW     = True   # AI reviews won/lost trades for learning
AI_SESSION_PULSE         = True   # Macro pulse broadcast after scan

# Spread Thresholds (XAUUSDm set to 300 points for Exness 3-decimal gold: $0.30 normal spread)
NORMAL_SPREAD_POINTS = {
    "EURUSDm": 15,
    "GBPUSDm": 20,
    "USDJPYm": 20,
    "AUDUSDm": 15,
    "USDCADm": 20,
    "EURJPYm": 25,
    "GBPJPYm": 50,
    "XAUUSDm": 300,
    "BTCUSDm": 1400,
    "ETHUSDm": 140,
    "SOLUSDm": 6000,
    "XRPUSDm": 1000,
    "LTCUSDm": 180,
}
MAX_SPREAD_MULT = 3

# Strategy Parameters
ENTRY_ZONE_MAX  = 0.5    # Longs only in lower 50% of 4H dealing range (discount), shorts upper 50% (premium)
AOI_TOLERANCE   = 0.0025 # 0.25% zone tolerance
AOI_MIN_TOUCHES = 2
SWING_WINDOW    = 3
PSYCH_PIP_TOL   = 10

# EMAs
EMA_FAST  = 9
EMA_SLOW  = 21
EMA_TREND = 200

# RSI Entry Bounds
RSI_LONG_MIN  = 32
RSI_LONG_MAX  = 68
RSI_SHORT_MIN = 32
RSI_SHORT_MAX = 68

# Scan Schedule (4H candle closes UTC)
SCAN_HOURS = [0, 4, 8, 12, 16, 20]

# Dry run mode (False = live execution)
DRY_RUN = False

# Weekend analysis
WEEKEND_ANALYSIS_HOUR = 8
