"""
Institutional Risk & Position Sizing Manager — v3.2 ($10+ High Growth Edition)
Implements:
  1. Strict Risk-Parity Lot Sizing targeting $10.00+ payouts per win
  2. Dynamic tick/contract valuation across Forex, Crypto, and Metals (Gold)
  3. Safety Guards (Balance Floor, Max Risk % Cap, Max Dollar Cap)
  4. Minimum Dollar Risk Floor (MIN_RISK_USD) for micro-account acceleration
"""

import math
import config
import bridge_client as bridge
from config import (
    USE_FIXED_LOT, FIXED_LOT, RISK_PERCENT,
    MAX_RISK_PCT, MIN_BALANCE, MAX_RISK_USD,
)

# Standard tick values ($ per 1 pip per 1.0 standard lot) fallback
_PIP_VALUE_PER_LOT = {
    "EURUSDm": 10.0,
    "GBPUSDm": 10.0,
    "XAUUSDm": 100.0,   # Gold: $1 move = $100 per lot
    "USDJPYm": 6.8,     # ~$6.80/pip at ~147 USDJPY
    "GBPJPYm": 6.8,
    "BTCUSDm": 1.0,     # 1 lot = 1 BTC, $1 move = $1 profit/loss per lot
    "ETHUSDm": 1.0,     # 1 lot = 1 ETH, $1 move = $1 profit/loss per lot
}


def get_pip_size(symbol):
    if "JPY" in symbol:
        return 0.01
    if "XAU" in symbol:
        return 1.0
    if "BTC" in symbol:
        return 1.0
    if "ETH" in symbol:
        return 0.1
    return 0.0001


def calculate_lot_size(symbol, sl_dist, balance, target_risk_usd=None, risk_pct=None, max_risk_pct=None):
    """
    Calculates exact lot size so the dollar risk equals target_risk_usd (or risk_pct / RISK_PERCENT of balance).
    Enforces minimum risk floor of $3.50 on micro accounts so 1:3.0 RR yields $10.00+ on winning trades.
    Returns (lot, risk_usd, risk_pct, warning_msg).
    """
    if balance < MIN_BALANCE:
        return None, 0.0, 0.0, f"Balance ${balance:.2f} below minimum ${MIN_BALANCE:.2f}"

    if sl_dist <= 0:
        return None, 0.0, 0.0, "Invalid stop loss distance (0 or negative)"

    # Determine desired dollar risk
    min_risk_floor = getattr(config, "MIN_RISK_USD", 3.50)
    
    if target_risk_usd is not None:
        risk_usd = min(target_risk_usd, MAX_RISK_USD)
    elif not USE_FIXED_LOT:
        # Scale with balance: risk_pct of balance, with a minimum floor of $3.50 (capped at MAX_RISK_USD)
        active_pct = risk_pct if risk_pct is not None else RISK_PERCENT
        calculated_risk = balance * (active_pct / 100.0)
        risk_usd = max(min_risk_floor, calculated_risk)
        risk_usd = min(risk_usd, MAX_RISK_USD)
    else:
        active_pct = risk_pct if risk_pct is not None else RISK_PERCENT
        risk_usd = max(min_risk_floor, min(MAX_RISK_USD, balance * (active_pct / 100.0 if active_pct > 0 else 0.05)))

    # Fetch live symbol specifications from MT5 bridge if available
    contract_size = None
    tick_value = None
    tick_size = None
    vol_min = 0.01
    vol_max = 100.0
    vol_step = 0.01

    try:
        sinfo = bridge.get_symbol_info(symbol)
        if sinfo and "error" not in sinfo:
            contract_size = sinfo.get("contract_size")
            tick_value = sinfo.get("tick_value")
            tick_size = sinfo.get("tick_size")
            vol_min = sinfo.get("volume_min", 0.01)
            vol_max = sinfo.get("volume_max", 100.0)
            vol_step = sinfo.get("volume_step", 0.01)
    except Exception:
        pass

    # Lot size calculation
    if tick_value and tick_size and tick_size > 0:
        ticks_at_risk = sl_dist / tick_size
        loss_per_lot = ticks_at_risk * tick_value
        raw_lot = risk_usd / max(loss_per_lot, 1e-6)
    else:
        # Fallback using pip valuation
        pip_size = get_pip_size(symbol)
        pips_at_risk = sl_dist / pip_size
        pip_val = _PIP_VALUE_PER_LOT.get(symbol, 10.0)
        loss_per_lot = pips_at_risk * pip_val
        raw_lot = risk_usd / max(loss_per_lot, 1e-6)

    # Round to broker volume step
    step = vol_step if vol_step > 0 else 0.01
    lot = round(round(raw_lot / step) * step, 2)
    lot = max(vol_min, min(vol_max, lot))

    # Calculate actual dollar risk with clamped lot
    if tick_value and tick_size and tick_size > 0:
        actual_risk_usd = lot * (sl_dist / tick_size) * tick_value
    else:
        pip_size = get_pip_size(symbol)
        actual_risk_usd = lot * (sl_dist / pip_size) * _PIP_VALUE_PER_LOT.get(symbol, 10.0)

    risk_pct = round((actual_risk_usd / balance) * 100.0, 1)

    warn = None
    # Institutional Capital Protection Guard: Enforce MAX_RISK_PCT and MAX_RISK_USD
    # (a method can pass a tighter cap, e.g. momentum trades never risk more than MOMENTUM_MAX_RISK_PCT)
    cap_pct = MAX_RISK_PCT if max_risk_pct is None else min(MAX_RISK_PCT, max_risk_pct)
    if risk_pct > cap_pct or actual_risk_usd > MAX_RISK_USD:
        max_allowed_dollar = min(MAX_RISK_USD, balance * (cap_pct / 100.0))
        safe_raw_lot = max_allowed_dollar / max(loss_per_lot, 1e-6)
        # Round DOWN: rounding to nearest turned 0.0067 lot into 0.01 and let a 30% risk through a 20% cap.
        safe_lot = round(math.floor(safe_raw_lot / step + 1e-9) * step, 2)

        if safe_lot < vol_min:
            # Fatal Risk Mismatch: The minimum broker order size carries more risk than the account allows.
            # Never execute orders that exceed account safety caps (e.g., Gold on micro accounts).
            return None, 0.0, 0.0, (
                f"REJECTED: Minimum volume ({vol_min}) on {symbol} risks ${actual_risk_usd:.2f} ({risk_pct}% of balance), "
                f"exceeding max safety limit of {cap_pct}% (${max_allowed_dollar:.2f}). "
                f"Stop loss is too wide for account equity."
            )

        lot = min(vol_max, max(vol_min, safe_lot))
        if tick_value and tick_size and tick_size > 0:
            actual_risk_usd = lot * (sl_dist / tick_size) * tick_value
        else:
            pip_size = get_pip_size(symbol)
            actual_risk_usd = lot * (sl_dist / pip_size) * _PIP_VALUE_PER_LOT.get(symbol, 10.0)
        risk_pct = round((actual_risk_usd / balance) * 100.0, 1)
        warn = f"Risk scaled down to {risk_pct}% (${actual_risk_usd:.2f}) to obey {cap_pct}% safety cap"

    return lot, round(actual_risk_usd, 2), risk_pct, warn
