"""
HTTP client for the MT5 bridge (mt5_bridge.py).
The bridge can be running on:
  - localhost:5001 (local PC or Wine on Oracle)
  - A Cloudflare tunnel URL (bridge on user's PC, exposed to internet)

Set BRIDGE_URL in config.py.

The bridge serves one request at a time and MT5 under Wine can stall for several seconds, so:
  - reads retry with back-off,
  - idempotent actions (close / modify / switch) retry,
  - orders are NEVER blindly re-sent: on a timeout we look for the new position in MT5 and adopt
    its ticket if the order actually filled (otherwise a filled trade would go untracked).
"""

import time

import requests
from config import BRIDGE_URL

TIMEOUT = 25          # reads
ACTION_TIMEOUT = 45   # close / modify
ORDER_TIMEOUT = 60    # order send
SWITCH_TIMEOUT = 60   # account login


def _get(path, timeout=TIMEOUT, retries=3):
    last = None
    for i in range(retries):
        try:
            return requests.get(f"{BRIDGE_URL}{path}", timeout=timeout).json()
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _post(path, payload, timeout=ACTION_TIMEOUT, retries=2):
    last = None
    for i in range(retries):
        try:
            return requests.post(f"{BRIDGE_URL}{path}", json=payload, timeout=timeout).json()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    return {"error": str(last)}


def health():
    try:
        return _get("/health")
    except Exception as e:
        return {"error": str(e)}


def get_account():
    h = health()
    if not h or "error" in h or not h.get("connected"):
        return None
    return h


def get_positions():
    """Returns the list of open positions, or None if the bridge could not be reached.
    Callers must treat None as "unknown" — never as "everything is closed"."""
    try:
        data = _get("/positions")
        if "positions" not in data:
            return None
        return data["positions"]
    except Exception:
        return None


def get_price(symbol):
    """Latest M1 close (bid) for a symbol, or None."""
    try:
        bars = _get(f"/ohlcv/{symbol}/M1/1").get("bars")
        return float(bars[-1]["close"]) if bars else None
    except Exception:
        return None


def get_symbol_info(symbol):
    try:
        return _get(f"/symbol/{symbol}")
    except Exception:
        return None


def _find_new_position(symbol, known_tickets, attempts=4):
    """After an order request failed in transit, check whether MT5 opened it anyway."""
    for _ in range(attempts):
        time.sleep(3)
        pos = get_positions()
        if pos is None:
            continue
        new = [p for p in pos if p.get("symbol") == symbol and p.get("ticket") not in known_tickets]
        if new:
            p = max(new, key=lambda x: x.get("time", 0))
            return {"ticket": p["ticket"], "price": p.get("open_price"), "volume": p.get("volume"),
                    "symbol": symbol, "reconciled": True}
    return None


def place_order(symbol, direction=None, lot=0.01, sl=0.0, tp=0.0, side=None, stop_loss=None, take_profit=None, comment="FXAlexG Bot"):
    """
    Place a market order with hard SL and TP.
    Accepts both (symbol, direction, lot, sl, tp) and keyword arguments.
    """
    if side is None:
        if direction in ("bullish", "long", "buy"):
            side = "buy"
        else:
            side = "sell"

    final_sl = stop_loss if stop_loss is not None else sl
    final_tp = take_profit if take_profit is not None else tp
    payload = {
        "symbol":      symbol,
        "side":        side.lower(),
        "lot":         float(lot),
        "stop_loss":   float(final_sl) if final_sl else 0.0,
        "take_profit": float(final_tp) if final_tp else 0.0,
        "comment":     comment,
    }

    before = get_positions()
    known = {p["ticket"] for p in before} if before else set()
    try:
        return requests.post(f"{BRIDGE_URL}/order", json=payload, timeout=ORDER_TIMEOUT).json()
    except Exception as e:
        # Lost the reply — the order may still have been filled. NEVER re-send: the single-threaded
        # bridge can still be executing the first request, so "not visible yet" does not mean
        # "not filled", and a resend would double the position.
        if before is not None:
            found = _find_new_position(symbol, known, attempts=8)
            if found:
                print(f"[bridge] order reply lost ({e}) but position #{found['ticket']} exists — adopted.")
                return found
        return {"error": f"order reply lost ({e}); no new position seen — NOT resent, check MT5"}


def spread_ok(symbol):
    """Return True if current spread is within 3x the normal baseline."""
    from config import NORMAL_SPREAD_POINTS, MAX_SPREAD_MULT
    info = get_symbol_info(symbol)
    if not info or "spread" not in info:
        return True
    live_spread = info["spread"]
    baseline    = NORMAL_SPREAD_POINTS.get(symbol, 50)
    return live_spread <= baseline * MAX_SPREAD_MULT


def is_online():
    h = health()
    return h is not None and "error" not in h and h.get("connected", False)


def get_position_result(ticket):
    """Fetch realized P&L for a closed position by its order/position ticket."""
    try:
        return _get(f"/history/{ticket}").get("profit")
    except Exception:
        return None


def close_position(ticket):
    res = _post("/close", {"ticket": int(ticket)})
    # A retry after a lost reply can find the position already gone — that means it closed.
    if res and "error" in res and "not found" in str(res["error"]).lower():
        return {"closed_ticket": int(ticket), "already_closed": True}
    return res


def partial_close(ticket, volume):
    """Close a partial volume of a position. Not retried (a retry could close twice)."""
    return _post("/close", {"ticket": int(ticket), "volume": float(volume)}, retries=1)


def modify_sl(ticket, new_sl, new_tp=None):
    """Modify the SL (and optionally TP) of an open position."""
    payload = {"ticket": int(ticket), "stop_loss": float(new_sl)}
    if new_tp is not None:
        payload["take_profit"] = float(new_tp)
    return _post("/modify", payload)


def switch_account(login, password, server):
    """Switch MT5 to a different account via the bridge."""
    return _post("/switch", {
        "login": int(login),
        "password": str(password) if password else "",
        "server": str(server),
    }, timeout=SWITCH_TIMEOUT)


def get_report(timeframe="today"):
    """Fetch PnL report for 'today' or 'week'."""
    try:
        return _get(f"/report/{timeframe}")
    except Exception as e:
        return {"error": str(e)}
