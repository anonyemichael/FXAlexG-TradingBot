"""
FXAlexG Trading Bot — Institutional Edition v3.1
Main orchestration loop, multi-timeframe scanner, position manager,
and Telegram interaction interface.

v3.1 Upgrades:
  - Integrated 24h re-entry cooldown on symbols after recent losses
  - Active high-impact economic news filter enforcement
  - Strict A+ setup execution with ADX market regime filtering
  - Adaptive crypto/forex stop loss sizing
"""

import time
import json
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
import bridge_client as bridge
import data_provider as dp
import strategy
import risk_manager
import trade_logger
import news_filter
import notifier
import ai_engine
import account_manager
import trend_strategy

ACCOUNT_WIZARDS = {}  # {chat_id: {"step": "...", "server": "...", "login": 123, ...}}

# [v3.2] Setup deduplication tracker
_last_signaled = {}  # {symbol: (direction, swing_eq)}
import weekly_analyst

BOT_DIR = Path(__file__).parent
ACTIVE_ACCOUNT_FILE = BOT_DIR / "active_account.json"


def load_active_account():
    if ACTIVE_ACCOUNT_FILE.exists():
        try:
            data = json.loads(ACTIVE_ACCOUNT_FILE.read_text())
            return data.get("active_account", config.ACTIVE_ACCOUNT)
        except Exception:
            pass
    return config.ACTIVE_ACCOUNT


def save_active_account(acc_key):
    try:
        ACTIVE_ACCOUNT_FILE.write_text(json.dumps({"active_account": acc_key}))
    except Exception as e:
        print(f"[config] Failed to save active account: {e}")


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def is_forex_market_open() -> bool:
    """Returns True if Forex/Metals markets are open (Sunday 21:00 UTC to Friday 21:00 UTC)."""
    now = datetime.now(timezone.utc)
    wd = now.weekday()
    h = now.hour
    if wd == 5:  # Saturday
        return False
    if wd == 6 and h < 21:  # Sunday before 21:00 UTC
        return False
    if wd == 4 and h >= 21:  # Friday after 21:00 UTC
        return False
    return True


def current_kill_zone():
    """
    Returns current active ICT Kill Zone session or None.
    Strictly respects weekend closures (Kill Zones only exist when Forex markets are open).
    London: 07:00 - 10:00 UTC
    New York: 12:00 - 15:00 UTC
    Asian: 00:00 - 04:00 UTC
    """
    if not is_forex_market_open():
        return None
    now = datetime.now(timezone.utc)
    h = now.hour
    if 7 <= h < 10:
        return "london"
    elif 12 <= h < 15:
        return "new_york"
    elif 0 <= h < 4:
        return "asian"
    return None


_close_lookup_misses = {}   # {ticket: consecutive polls where the deal history was unavailable}
_action_failures = {}       # {(ticket, action): failed attempts} — stops TTL/TP1 retry loops
_news_alerted = set()       # {(symbol, title)} — one Telegram alert per news event


def _master_login():
    prof = config.ACCOUNTS.get(load_active_account(), {})
    return int(prof.get("login") or 0) or None


def _tag(login=None):
    """'🎯 SMC (yours) · #477674441' — which bot a Telegram message is about."""
    login = login or _master_login()
    acc = account_manager.get_account(login) if login else None
    return notifier.bot_tag(acc or {"login": login})


def _restore_master():
    prof = config.ACCOUNTS.get(load_active_account(), {})
    if prof.get("login"):
        bridge.switch_account(prof.get("login"), prof.get("password"), prof.get("server"))


def check_closed_positions(login=None, is_master=True):
    """Polls database for open trades of the account the bridge is logged into and reconciles
    them with its MT5 positions."""
    if login is None:
        login = _master_login()
    open_tickets = trade_logger.get_open_trade_tickets(login, is_master)
    if not open_tickets:
        return
    live_positions = bridge.get_positions()
    if live_positions is None:
        return  # bridge unreachable — we don't know what's open, so don't close anything
    live_tickets = {p["ticket"] for p in live_positions}
    for ticket in open_tickets:
        if ticket not in live_tickets:
            profit = bridge.get_position_result(ticket)
            if profit is None:
                # History not available yet (or bridge hiccup). Retry for ~10 min before giving up,
                # instead of immediately recording a fake $0.00 result.
                misses = _close_lookup_misses.get(ticket, 0) + 1
                _close_lookup_misses[ticket] = misses
                if misses >= 40:
                    trade_logger.log_trade_closed(ticket, None)
                    _close_lookup_misses.pop(ticket, None)
                    notifier.send(f"<b>{_tag(login)}</b>\n⚠️ Trade #{ticket} is no longer open but its P&L could not be read from MT5. Check the terminal history.")
                    print(f"[{_ts()}] Trade #{ticket} closed — P&L unknown.")
                continue
            _close_lookup_misses.pop(ticket, None)
            if profit is not None:
                trade_logger.log_trade_closed(ticket, profit)
                pnl_str = str(round(profit, 2))
                sign = "+" if profit > 0 else ""
                trade_rec = trade_logger.get_trade_by_ticket(ticket) or {}
                ai_note = ""
                if getattr(config, "AI_POST_TRADE_REVIEW", False) and getattr(config, "AI_ENABLED", False):
                    try:
                        trade_rec["profit"] = profit
                        ai_rev = ai_engine.review_trade_outcome(trade_rec)
                        if ai_rev:
                            ai_note = f"\n\n🧠 <b>AI Trade Review:</b>\n<i>{ai_rev}</i>"
                    except Exception as e:
                        print(f"[{_ts()}] AI post-mortem error: {e}")
                res_emoji = "✅ WIN" if profit > 0 else ("⚪ BREAKEVEN" if profit == 0 else "❌ LOSS")
                notifier.send(f"<b>{_tag(trade_rec.get('login') or login)}</b>\n🏁 <b>CLOSED {trade_rec.get('symbol', '')}</b> — {res_emoji} <b>${sign}{pnl_str}</b>\nTicket #{ticket}{ai_note}")
                print(f"[{_ts()}] Trade #{ticket} closed with P&L: ${sign}{pnl_str}")
                # Policy evolution rewrites trading rules from a handful of trades, which is noise.
                # Off by default; can still be run on demand from Telegram (🧬 Agent Evolution).
                if getattr(config, "AI_AUTO_TRAIN", False):
                    try:
                        import ai_trainer
                        print(f"[{_ts()}] 🧬 Triggering autonomous agent self-training cycle...")
                        ai_trainer.train_agent(notify=True)
                    except Exception as te:
                        print(f"[{_ts()}] Auto-training failed: {te}")


def manage_open_positions(login=None, is_master=True):
    """Manages TP1 partial profit, moves SL to breakeven, trails stop, and enforces TTL."""
    open_tickets = trade_logger.get_open_trade_tickets(login or _master_login(), is_master)
    if not open_tickets:
        return
    live_positions = bridge.get_positions()
    if not live_positions:
        return
    live_by_ticket = {p["ticket"]: p for p in live_positions}

    now = datetime.now(timezone.utc)

    for ticket in open_tickets:
        trade = trade_logger.get_trade_by_ticket(ticket)
        if not trade:
            continue

        pos = live_by_ticket.get(ticket)
        if not pos:
            continue

        if trade.get("trigger") in trend_strategy.TRIGGERS:
            continue  # trend trades are managed once per day by manage_trend_positions()

        entry     = trade["entry"]
        tp1       = trade.get("tp1")
        tp2       = trade.get("tp2")
        direction = trade["direction"]
        symbol    = trade["symbol"]
        opened_at_str = trade.get("opened_at")

        # 1. Trade TTL Check (Max lifespan). 0 / None disables it.
        ttl_h = getattr(config, "MAX_TRADE_LIFESPAN_HOURS", 0)
        is_crypto = any(c in symbol.upper() for c in ("BTC", "ETH", "SOL", "XRP", "LTC"))
        if opened_at_str and ttl_h and (is_crypto or is_forex_market_open()):
            try:
                opened_at = datetime.fromisoformat(opened_at_str).replace(tzinfo=timezone.utc)
                hours_open = (now - opened_at).total_seconds() / 3600.0
                if hours_open > ttl_h and not trade_logger.is_partial_closed(ticket) \
                        and _action_failures.get((ticket, "ttl"), 0) < 5:
                    print(f"[{_ts()}] TTL Exceeded ({hours_open:.1f}h) for {symbol} #{ticket}. Closing.")
                    res = bridge.close_position(ticket)
                    if res and "error" not in res:
                        notifier.send(f"<b>{_tag(trade.get('login'))}</b>\n🕒 Closed {symbol} #{ticket} — lifespan exceeded ({ttl_h}h).")
                    else:
                        n = _action_failures[(ticket, "ttl")] = _action_failures.get((ticket, "ttl"), 0) + 1
                        err = res.get("error", "no response") if res else "no response"
                        print(f"[{_ts()}] TTL close failed for #{ticket} (attempt {n}/5): {err}")
                        if n == 5:
                            notifier.send(f"<b>{_tag(trade.get('login'))}</b>\n⚠️ Could not close {symbol} #{ticket} after lifespan expiry: {err}. Leaving it on its hard SL/TP.")
                    continue
            except Exception as e:
                print(f"[{_ts()}] TTL error: {e}")

        if not tp1 or not entry:
            continue

        current_price = pos["current_price"]
        is_partially_closed = trade_logger.is_partial_closed(ticket)

        # 2. Check if price reached TP1 (1:1.2 RR)
        if not is_partially_closed:
            tp1_hit = (direction == "bullish" and current_price >= tp1) or \
                      (direction == "bearish" and current_price <= tp1)

            if tp1_hit and _action_failures.get((ticket, "tp1"), 0) < 3:
                try:
                    full_volume = pos["volume"]
                    sinfo = bridge.get_symbol_info(symbol) or {}
                    vol_min = float(sinfo.get("volume_min", 0.01) or 0.01)
                    vol_step = float(sinfo.get("volume_step", 0.01) or 0.01)
                    partial_volume = round(int(full_volume * config.PARTIAL_PCT / vol_step) * vol_step, 2)

                    # If the position is too small to split (e.g. 0.01 lot, or ETH's 0.1 minimum),
                    # do NOT close it all at TP1 — just protect it at breakeven and let it run to TP2.
                    can_split = partial_volume >= vol_min and (full_volume - partial_volume) >= vol_min - 1e-9
                    partial_profit = 0.0
                    if can_split and config.PARTIAL_PCT > 0:
                        print(f"[{_ts()}] TP1 HIT: {symbol} #{ticket} — closing {partial_volume} of {full_volume}")
                        close_result = bridge.partial_close(ticket, partial_volume)
                        if not close_result or "error" in close_result:
                            err = close_result.get("error", "unknown") if close_result else "no response"
                            n = _action_failures[(ticket, "tp1")] = _action_failures.get((ticket, "tp1"), 0) + 1
                            print(f"[{_ts()}] TP1 partial close failed for #{ticket} (attempt {n}/3): {err}")
                            if n == 3:
                                notifier.send(f"<b>{_tag(trade.get('login'))}</b>\n⚠️ TP1 partial close failed 3x for {symbol} #{ticket}: {err}")
                            continue
                        # bridge reports the whole position's floating P&L; scale to the closed part
                        partial_profit = close_result.get("profit", 0) * (partial_volume / full_volume)
                    else:
                        print(f"[{_ts()}] TP1 HIT: {symbol} #{ticket} — {full_volume} lot too small to split, moving SL to breakeven only")
                        partial_volume = 0.0

                    # Spread-aware breakeven buffer
                    if "BTC" in symbol:   be_buffer = 15.0
                    elif "ETH" in symbol: be_buffer = 1.0
                    elif "XAU" in symbol: be_buffer = 1.0
                    elif "JPY" in symbol: be_buffer = 0.04
                    else: be_buffer = 0.0004

                    breakeven_sl = entry + be_buffer if direction == "bullish" else entry - be_buffer

                    modify_result = bridge.modify_sl(ticket, breakeven_sl, new_tp=tp2)
                    if modify_result and "error" not in modify_result:
                        print(f"[{_ts()}] SL moved to breakeven: {breakeven_sl}")
                    else:
                        print(f"[{_ts()}] Breakeven SL modify: {modify_result}")

                    trade_logger.log_partial_close(ticket, partial_profit)
                    if partial_volume:
                        msg = (
                            f"<b>{_tag(trade.get('login'))}</b>\n🎯 <b>TP1 HIT — {symbol} #{ticket}</b>\n\n"
                            f"Secured {partial_volume} lot @ 1:{config.TP1_RR} | P&L: <b>+${partial_profit:.2f}</b>\n"
                            f"Remaining {round(full_volume - partial_volume, 2)} lot -> SL moved to Breakeven (+buffer)"
                        )
                    else:
                        msg = (
                            f"<b>{_tag(trade.get('login'))}</b>\n🎯 <b>TP1 HIT — {symbol} #{ticket}</b>\n\n"
                            f"Position ({full_volume} lot) is at minimum size, so nothing was closed.\n"
                            f"SL moved to Breakeven (+buffer); running to TP2."
                        )
                    notifier.send(msg)
                    print(f"[{_ts()}] {msg}")
                except Exception as e:
                    print(f"[{_ts()}] Partial TP error: {e}")

        # 3. Trailing Stop (15M EMA21) after TP1
        elif config.USE_TRAILING_STOP:
            try:
                df_15m = dp.get_15m(symbol)
                if df_15m is not None and len(df_15m) >= 25:
                    df_15m = strategy.add_indicators(df_15m)
                    ema21_15m = float(df_15m["ema21"].iloc[-2])
                    pip_size = risk_manager.get_pip_size(symbol)
                    buffer_val = config.TRAILING_STOP_BUFFER * pip_size

                    current_sl = pos.get("sl", 0.0)
                    if direction == "bullish":
                        trail_sl = round(ema21_15m - buffer_val, 5)
                        if trail_sl > current_sl and trail_sl > entry:
                            bridge.modify_sl(ticket, trail_sl, new_tp=tp2)
                    else:
                        trail_sl = round(ema21_15m + buffer_val, 5)
                        if (current_sl == 0 or trail_sl < current_sl) and trail_sl < entry:
                            bridge.modify_sl(ticket, trail_sl, new_tp=tp2)
            except Exception as e:
                print(f"[{_ts()}] Trailing stop error: {e}")


def check_ema_invalidation():
    """Permanently disabled to allow trades to reach true Take Profit targets without premature panic cuts."""
    pass


def _revalidate_at_market(setup):
    """Re-checks SL distance and R:R against the live price right before sending the order.
    Returns False if price already ran too far toward TP (or through SL)."""
    live = bridge.get_price(setup["symbol"])
    if live is None:
        return True  # can't check; keep the candle-based plan
    if setup.get("trigger") in trend_strategy.TRIGGERS:
        # Trend trades have no TP; the stop is a fixed ATR distance from the actual fill.
        setup["entry"] = live
        setup["sl"] = round(live - setup["sl_dist"], 6)
        return True
    buy = setup["direction"] == "bullish"
    risk = (live - setup["sl"]) if buy else (setup["sl"] - live)
    reward = (setup["tp2"] - live) if buy else (live - setup["tp2"])
    if risk <= 0 or reward <= 0:
        print(f"[{_ts()}] {setup['symbol']}: live price {live} already beyond SL/TP. Skipping.")
        return False
    rr = reward / risk
    if rr < config.MIN_RR:
        print(f"[{_ts()}] {setup['symbol']}: R:R at live price {live} is 1:{rr:.2f} (< {config.MIN_RR}). Skipping.")
        return False
    # size on the larger of planned / live distance so real risk never exceeds the target
    setup["sl_dist"] = max(setup["sl_dist"], risk)
    setup["rr"] = round(rr, 2)
    return True


def _dispatch_setup_to_accounts(setup, side, grade, score, has_news=False, methods=("trend",), kill_zone=None):
    """
    Executes a signal on every active account whose assigned strategy is in `methods`.
    Calculates dynamic proportional lot size per account based on individual equity and risk %.
    """
    symbol = setup["symbol"]
    if not _revalidate_at_market(setup):
        return
    active_accounts = [a for a in account_manager.get_active_accounts()
                       if (a.get("strategy") or "trend") in methods]
    if not active_accounts and account_manager.get_active_accounts():
        return  # accounts exist, just none assigned to this method

    if not active_accounts:
        acc = bridge.get_account()
        bal = acc.get("balance", 0.0) if acc else 0.0
        lot, risk_usd, risk_pct, warn = risk_manager.calculate_lot_size(symbol, setup["sl_dist"], bal)
        if not lot or lot <= 0:
            print(f"[{_ts()}] {symbol}: setup skipped by risk manager: {warn}")
            notifier.send(f"🚫 <b>{symbol} setup skipped</b> — {warn}")
            return
        order_res = bridge.place_order(symbol=symbol, side=side, lot=lot, sl=setup["sl"], tp=setup["tp2"], comment=f"FXAlexG {grade}")
        if order_res and "error" not in order_res:
            t = order_res.get("ticket")
            trade_logger.log_trade_opened(setup, lot, risk_usd, t, login=(acc or {}).get("login"))
            notifier.send_trade_opened(setup, lot, risk_usd, risk_pct)
        else:
            err = order_res.get("error", "no response") if order_res else "no response"
            print(f"[{_ts()}] {symbol}: order rejected by broker: {err}")
            notifier.send(f"❌ <b>{symbol} order rejected</b> — {err}")
        return

    curr_active = load_active_account()
    master_prof = config.ACCOUNTS.get(curr_active, {})
    total_executed = 0

    for acc in active_accounts:
        login = acc["login"]
        pw = acc["password"]
        srv = acc["server"]
        r_pct = acc.get("risk_pct", 1.0)
        label = acc.get("label", f"Account #{login}")

        try:
            # 1. Connect/Switch to account
            sw = bridge.switch_account(login, pw, srv)
            if not sw or "error" in sw:
                print(f"[{_ts()}] Multi-Account: Switch to #{login} failed: {sw}")
                continue

            acc_info = bridge.get_account()
            if not acc_info:
                continue

            bal = float(acc_info.get("balance", acc.get("balance", 0.0)))
            eq = float(acc_info.get("equity", bal))
            cur = acc_info.get("currency", "USD")
            account_manager.update_account_balance(login, bal, eq, cur)

            # Per-account limits (each account is its own experiment)
            acc_pos = bridge.get_positions()
            if acc_pos is None:
                print(f"[{_ts()}] Multi-Account: #{login} positions unreadable — skipped.")
                continue
            if any(p["symbol"] == symbol for p in acc_pos):
                print(f"[{_ts()}] Multi-Account: #{login} already holds {symbol} — skipped.")
                continue
            is_trend = setup.get("trigger") in trend_strategy.TRIGGERS
            max_open = getattr(config, "TREND_MAX_OPEN", 4) if is_trend else config.MAX_CONCURRENT
            if len(acc_pos) >= max_open:
                print(f"[{_ts()}] Multi-Account: #{login} has {len(acc_pos)} open (max {max_open}) — skipped.")
                continue
            if not is_trend:
                if trade_logger.get_daily_trade_count(login) >= config.MAX_TRADES_DAY:
                    print(f"[{_ts()}] Multi-Account: #{login} daily trade limit reached — skipped.")
                    continue
                if kill_zone and trade_logger.get_kill_zone_trade_count(kill_zone, login) >= config.MAX_TRADES_KILL_ZONE:
                    print(f"[{_ts()}] Multi-Account: #{login} {kill_zone} kill-zone limit reached — skipped.")
                    continue

            # 2. Dynamic lot sizing strictly adhering to this account's equity and risk %
            lot, risk_usd, actual_pct, warn = risk_manager.calculate_lot_size(
                symbol, setup["sl_dist"], bal, risk_pct=r_pct
            )
            if not lot or lot <= 0:
                print(f"[{_ts()}] Multi-Account: #{login} (${bal:.2f}) skipped: {warn}")
                notifier.send(f"<b>{notifier.bot_tag(acc)}</b>\n🚫 <b>{symbol} skipped</b> — {warn}", chat_id=acc.get("chat_id") or None)
                continue

            # 3. Send market order with hard MT5 broker-side SL/TP
            order_res = bridge.place_order(
                symbol=symbol,
                side=side,
                lot=lot,
                sl=setup["sl"],
                tp=setup["tp2"],
                comment=f"FXAlexG #{login}"
            )

            if order_res and "error" not in order_res:
                ticket = order_res.get("ticket")
                total_executed += 1
                trade_logger.log_trade_opened(setup, lot, risk_usd, ticket, login=login)
                notifier.send_trade_opened_for_account({**acc, "balance": bal}, setup, lot, risk_usd, ticket)
                print(f"[{_ts()}] Multi-Account: #{login} ({label}) -> {side.upper()} {lot} lot (Ticket #{ticket})")
            else:
                err = order_res.get("error", "execution error") if order_res else "no response"
                print(f"[{_ts()}] Multi-Account: Order failed for #{login}: {err}")
                notifier.send(f"<b>{notifier.bot_tag(acc)}</b>\n❌ <b>{symbol} order rejected</b> — {err}", chat_id=acc.get("chat_id") or None)

        except Exception as e:
            print(f"[{_ts()}] Multi-Account execution exception on #{login}: {e}")

    # 4. Always restore bridge connection to master monitoring profile
    if master_prof:
        bridge.switch_account(master_prof.get("login"), master_prof.get("password"), master_prof.get("server"))

    print(f"[{_ts()}] Multi-Account Dispatch Complete: {total_executed}/{len(active_accounts)} accounts filled.")


_trend_scanned = set()   # {(symbol, date)} — one entry check per symbol per daily candle


def run_trend_scan():
    """Daily trend-following entries (config.STRATEGY == 'trend'). Safe to call every hour:
    each symbol is evaluated once per new daily candle."""
    if not bridge.get_account():
        print(f"[{_ts()}] MT5 bridge offline — trend scan skipped.")
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Open-position / max-open / balance checks happen per account inside the dispatcher.
    in_use = {(a.get("strategy") or "trend") for a in account_manager.get_active_accounts()}
    variants = [v for v in trend_strategy.VARIANTS if v in in_use]
    for symbol in config.TREND_SYMBOLS:
        if (symbol, today) in _trend_scanned:
            continue
        df_d = dp.get_daily(symbol, count=300)
        if df_d is None:
            continue  # retry next hour
        _trend_scanned.add((symbol, today))
        for variant in variants:
            v = trend_strategy.VARIANTS[variant]
            setup = trend_strategy.entry_signal(symbol, df_d, variant)
            if not setup:
                print(f"[{_ts()}] Trend[{variant}] {symbol}: no new EMA{v['fast']}/{v['slow']} cross.")
                continue
            print(f"[{_ts()}] Trend[{variant}] {symbol}: EMA{v['fast']} crossed above EMA{v['slow']} — buying.")
            _dispatch_setup_to_accounts(setup, "buy", setup["grade"], setup["score"], methods=(variant,))


def manage_other_accounts():
    """Logs the bridge into each extra active account (not the master), reconciles closed trades,
    trails trend stops / exits, then logs back into the master. Runs hourly with the trend check."""
    master = _master_login()
    others = [a for a in account_manager.get_active_accounts() if int(a["login"]) != master]
    switched = False
    try:
        for acc in others:
            login = int(acc["login"])
            if not trade_logger.get_open_trade_tickets(login, False):
                continue
            sw = bridge.switch_account(login, acc["password"], acc["server"])
            switched = True
            if not sw or "error" in sw:
                print(f"[{_ts()}] Manage #{login}: switch failed: {sw}")
                continue
            info = bridge.get_account()
            if info:
                account_manager.update_account_balance(login, info.get("balance", 0.0), info.get("equity", 0.0), info.get("currency", "USD"))
            check_closed_positions(login, is_master=False)
            manage_open_positions(login, is_master=False)   # SMC: TP1 / breakeven / trailing / TTL
            manage_trend_positions(login, is_master=False)  # trend: chandelier stop / EMA exit
    finally:
        if switched:
            _restore_master()


def manage_trend_positions(login=None, is_master=True):
    """Raises trend-trade stops to the 3xATR chandelier level and exits when the EMAs cross back."""
    if login is None:
        login = _master_login()
    open_tickets = trade_logger.get_open_trade_tickets(login, is_master)
    if not open_tickets:
        return
    live_positions = bridge.get_positions()
    if not live_positions:
        return
    live_by_ticket = {p["ticket"]: p for p in live_positions}
    for ticket in open_tickets:
        trade = trade_logger.get_trade_by_ticket(ticket)
        pos = live_by_ticket.get(ticket)
        if not trade or not pos or trade.get("trigger") not in trend_strategy.TRIGGERS:
            continue
        symbol = trade["symbol"]
        df_d = dp.get_daily(symbol, count=300)
        action, new_sl = trend_strategy.manage(df_d, trade["opened_at"], pos.get("sl", 0.0), trade.get("trigger"))
        v = trend_strategy.VARIANTS[trend_strategy.TRIGGERS[trade.get("trigger")]]
        if action == "exit" and _action_failures.get((ticket, "trend_exit"), 0) < 5:
            res = bridge.close_position(ticket)
            if res and "error" not in res:
                notifier.send(f"<b>{_tag(login)}</b>\n📉 <b>Trend over — closed {symbol} #{ticket}</b>\nEMA{v['fast']} closed back below EMA{v['slow']}.")
            else:
                _action_failures[(ticket, "trend_exit")] = _action_failures.get((ticket, "trend_exit"), 0) + 1
                print(f"[{_ts()}] Trend exit failed for #{ticket}: {res}")
        elif action == "trail":
            res = bridge.modify_sl(ticket, round(new_sl, 6))
            if res and "error" not in res:
                print(f"[{_ts()}] Trend {symbol} #{ticket}: stop raised to {new_sl:.5f}")
            else:
                print(f"[{_ts()}] Trend {symbol} #{ticket}: stop raise failed: {res}")


def run_scan(kill_zone=None):
    """SMC scanner. Signals go to accounts assigned 'smc' (mechanical) and/or 'smc_ai' (AI veto).
    Balance, daily/kill-zone limits and open positions are checked per account in the dispatcher."""
    if not bridge.get_account():
        print(f"[{_ts()}] MT5 bridge offline — scan skipped.")
        return

    pairs_scanned = 0
    setups_found = 0

    forex_open = is_forex_market_open()
    for symbol in config.SYMBOLS:
        is_crypto = any(c in symbol.upper() for c in ("BTC", "ETH", "SOL"))
        if not forex_open and not is_crypto:
            continue  # Forex and Gold markets are closed on weekends

        pairs_scanned += 1
        print(f"[{_ts()}] Scanning {symbol}...")
        try:
            # 1. [v3.1] Re-Entry Cooldown Filter
            in_cooldown, hours_rem = trade_logger.is_in_cooldown(symbol, config.COOLDOWN_HOURS)
            if in_cooldown:
                print(f"[{_ts()}] {symbol}: In post-loss cooldown ({hours_rem}h remaining). Skipping.")
                continue

            # 2. [v3.1] High-Impact Economic News Filter
            has_news, news_title = news_filter.has_high_impact_news_soon(symbol, config.NEWS_LOOKOUT_MINS)
            if has_news:
                print(f"[{_ts()}] {symbol}: High-impact news upcoming ('{news_title}'). Skipping.")
                if (symbol, news_title) not in _news_alerted:
                    _news_alerted.add((symbol, news_title))
                    notifier.send(f"⏸️ <b>Scan Paused on {symbol}</b> — High-impact news event upcoming: <i>{news_title}</i>.")
                continue

            # 2b. [v3.2] Spread Check — skip if spread is too wide
            if not bridge.spread_ok(symbol):
                print(f"[{_ts()}] {symbol}: Spread too wide. Skipping.")
                continue

            # 3. Market Data Retrieval
            df_w  = dp.get_weekly(symbol)
            df_d  = dp.get_daily(symbol)
            df_4h = dp.get_4h(symbol)
            df_15m = dp.get_15m(symbol)

            if df_w is None or df_d is None or df_4h is None:
                continue

            # 4. Multi-Timeframe Strategy Analysis
            setup = strategy.analyze_pair(
                symbol, df_w, df_d, df_4h, df_15m=df_15m,
                in_kill_zone=(kill_zone is not None),
            )

            if not setup:
                print(f"[{_ts()}] {symbol}: no valid setup.")
                trade_logger.log_scan(symbol, "F", 0, "-", "no_setup")
                continue

            grade = setup["grade"]
            score = setup["score"]
            trade_logger.log_scan(symbol, grade, score, setup["direction"], "setup_found")

            # Grade Gate: A+ or high-scoring B+ for AI confirmation
            is_a_plus = (grade == config.MIN_GRADE)
            is_b_plus_ai_candidate = (getattr(config, "AI_UPGRADE_B_PLUS", False) and grade == "B+" and score >= 80)

            if not (is_a_plus or is_b_plus_ai_candidate):
                print(f"[{_ts()}] {symbol}: Setup found ({grade}, {score} pts), below threshold.")
                continue

            # [v3.2] Dedup — skip if same setup already signaled
            setup_key = (setup["direction"], round(setup.get("swing_eq", 0), 3))
            if _last_signaled.get(symbol) == setup_key:
                print(f"[{_ts()}] {symbol}: Duplicate setup (same direction + swing range). Skipping.")
                continue

            # ── Routing ──
            # 'smc' accounts   : every A+ setup (mechanical rules only)
            # 'smc_ai' accounts: A+ setups the AI approves, plus B+ setups the AI upgrades (old behaviour)
            methods = []
            if is_a_plus:
                methods.append("smc")
            ai_wanted = getattr(config, "AI_ENABLED", False) and any(
                (a.get("strategy") or "trend") == "smc_ai" for a in account_manager.get_active_accounts())
            if ai_wanted:
                print(f"[{_ts()}] 🧠 Querying AI for {symbol} review...")
                sym_info = bridge.get_symbol_info(symbol) or {}
                market_ctx = {
                    "adx": setup.get("adx", "N/A"),
                    "spread": sym_info.get("spread", "Normal"),
                    "news": "Clean calendar",
                }
                ai_eval = ai_engine.evaluate_setup(setup, market_ctx)
                if ai_eval.get("provider") == "Safety Fail-Safe":
                    time.sleep(5)  # Gemini 503/timeout is usually momentary — one retry
                    ai_eval = ai_engine.evaluate_setup(setup, market_ctx)
                verdict = ai_eval.get("verdict", "CAUTION")
                conf = ai_eval.get("confidence", 0)
                provider = ai_eval.get("provider", "AI")
                trap = ai_eval.get("trap_detected", False)
                ai_down = provider == "Safety Fail-Safe"
                print(f"[{_ts()}] AI Evaluation ({provider}): {verdict} ({conf}% conf) | Trap: {trap}")
                if not ai_down:
                    setup["ai_verdict"], setup["ai_conf"] = verdict, conf
                    setup["ai_narrative"], setup["ai_provider"] = ai_eval.get("narrative", ""), provider
                    min_conf = 85 if not is_a_plus else getattr(config, "AI_MIN_CONFIDENCE", 70)
                    if verdict == "APPROVED" and not trap and conf >= min_conf:
                        methods.append("smc_ai")
                        if not is_a_plus:
                            setup["grade"] = grade = "A+ (AI Confirmed)"
                    else:
                        print(f"[{_ts()}] {symbol}: AI vetoed for smc_ai accounts ({verdict}, {conf}%).")
                elif is_a_plus:
                    # AI unreachable: smc_ai accounts do NOT trade it. That account measures the AI-gated
                    # system as designed (fail-safe reject), including what it misses during outages;
                    # the plain 'smc' account still takes the setup.
                    print(f"[{_ts()}] {symbol}: AI unreachable — smc_ai skips, smc accounts still trade it.")
                else:
                    continue  # AI down: B+ can't be upgraded; retry next scan (not marked as signaled)

            if not methods:
                _last_signaled[symbol] = setup_key
                print(f"[{_ts()}] {symbol}: no account method accepted this setup.")
                continue

            _last_signaled[symbol] = setup_key
            setups_found += 1

            # 5. Multi-Account Execution Dispatch
            side = "buy" if setup["direction"] == "bullish" else "sell"
            _dispatch_setup_to_accounts(setup, side, grade, score, methods=tuple(methods), kill_zone=kill_zone)

        except Exception as e:
            print(f"[{_ts()}] Scan error on {symbol}: {e}")
            traceback.print_exc()

    # Telegram summary: only when something was found, or at 4H closes outside kill zones
    # (kill zones scan every 15 min, which used to flood the chat).
    if setups_found or not kill_zone:
        open_trades = len(bridge.get_positions() or [])
        day_count = trade_logger.get_daily_trade_count()
        notifier.send_scan_summary(pairs_scanned, setups_found, open_trades, day_count)


def _send_welcome_menu(chat_id=None):
    acc = bridge.get_account()
    bal_str = f"${acc.get('balance', 0.0):.2f}" if acc else "$20.00"
    login_str = str(acc.get("login", "436015341")) if acc else "436015341"
    server_str = str(acc.get("server", "Exness-MT5Trial10")) if acc else "Exness-MT5Trial10"
    active_count = len(account_manager.get_active_accounts())
    
    notifier.send(
        "👋 <b>Welcome to FXAlexG Institutional ICT Bot!</b>\n\n"
        f"🏦 <b>Active Broker:</b> {server_str}\n"
        f"👤 <b>Master Account:</b> <code>{login_str}</code>\n"
        f"💰 <b>Master Balance:</b> <b>{bal_str} USD</b>\n"
        f"👥 <b>Connected Accounts:</b> <b>{active_count} Active</b>\n"
        f"⚡ <b>AutoTrading:</b> <b>ACTIVE & READY ✅</b>\n\n"
        "<b>👇 Tap any 1-Click Shortcut Button below:</b>\n\n"
        "• <b>➕ Connect Account</b> — Add your MT5 account in 30s\n"
        "• <b>🏆 Scoreboard</b> — Which bot is winning (P&L per account)\n"
        "• <b>👥 My Accounts</b> — View, pause or manage all accounts\n"
        "• <b>🧬 Agent Evolution</b> — AI Generation & empirical axioms\n"
        "• <b>🏦 Balance & Status</b> — Live MT5 equity and margin\n"
        "• <b>📊 Market Pulse</b> — Multi-asset institutional SMC flow\n"
        "• <b>🥇 Gold / 💶 EUR/USD / ₿ Bitcoin</b> — Instant pair scans\n"
        "• <b>📈 P&L Report</b> — Daily and weekly profit history\n\n"
        "💬 <i>You can also ask me any trading questions directly!</i>",
        keyboard=True,
        chat_id=chat_id
    )



def _handle_agent_evolution():
    """Manual trigger / status check for AI Agent Evolution."""
    notifier.send_typing()
    try:
        import ai_trainer
        notifier.send("🧬 <b>Initiating Autonomous Agent Self-Training Epoch...</b>\n<i>Analyzing trade performance matrix and recalculating policy axioms...</i>")
        policy = ai_trainer.train_agent(notify=False)
        matrix = ai_trainer.get_performance_matrix()
        axioms_str = "\n".join(f"  • <i>\"{a}\"</i>" for a in policy.get("axioms", []))
        msg = (
            f"🧬 <b>AI AGENT BRAIN — Generation {policy.get('generation', 1)}</b>\n\n"
            f"<b>Active Policy:</b> {policy.get('name', 'Adaptive SMC')}\n"
            f"<b>Total Training Set:</b> {matrix.get('total_trades', 0)} Real Trades\n"
            f"<b>Win Rate:</b> {matrix.get('win_rate', 0.0)}% | Net: ${matrix.get('net_pnl', 0.0):.2f}\n"
            f"<b>Min Confidence:</b> {policy.get('min_confidence', 75)}%\n"
            f"<b>Noise Protection SL Floor:</b> {policy.get('sl_min_forex_pips', 12)} pips\n\n"
            f"<b>Current Operational Axioms:</b>\n{axioms_str}\n\n"
            f"<i>Agent continuously adapts policy weights after each trade.</i>"
        )
        notifier.send(msg)
    except Exception as e:
        notifier.send(f"❌ Evolution Error: {e}")

def _handle_ai_pulse():
    notifier.send_typing()
    try:
        summary_list = []
        for s in config.SYMBOLS[:8]:
            d = dp.get_daily(s, 20)
            if d is not None:
                p = float(d["close"].iloc[-1])
                summary_list.append(f"{s}: Price={p:.5f}")
        pulse = ai_engine.generate_session_pulse(summary_list)
        notifier.send(pulse)
    except Exception as e:
        notifier.send(f"❌ Session Pulse Error: {e}")


def _handle_ai_status():
    notifier.send_typing()
    try:
        _, prov = ai_engine.query_ai("Ping", "Reply 'OK'")
        pool_count = len(getattr(config, "GEMINI_API_KEYS", []))
        notifier.send(
            f"✅ <b>100% Google Gemini Engine Online</b>\n\n"
            f"• Active Engine: <b>{prov}</b>\n"
            f"• High-Availability Pool: <b>{pool_count} API Keys Active</b>\n"
            f"• Gatekeeper: <b>Full Decision Authority (min {config.AI_MIN_CONFIDENCE}% conf)</b>\n"
            f"• B+ Upgrader: <b>{'Enabled' if config.AI_UPGRADE_B_PLUS else 'Disabled'}</b>\n"
            f"• Capital Protection Guard: <b>Active (Auto-Veto excessive risk)</b>"
        )
    except Exception as e:
        notifier.send(f"❌ AI Status Check Failed: {e}")


def _handle_ai_symbol_analysis(matched_sym: str):
    notifier.send_typing()
    try:
        df_w = dp.get_weekly(matched_sym, count=30)
        df_d = dp.get_daily(matched_sym, count=50)
        df_4h = dp.get_4h(matched_sym, count=50)

        if df_w is None or df_d is None or df_4h is None:
            notifier.send(f"❌ Data feed unavailable for {matched_sym}.")
            return

        df_w = strategy.add_indicators(df_w)
        df_d = strategy.add_indicators(df_d)
        df_4h = strategy.add_indicators(df_4h)

        price = float(df_4h["close"].iloc[-1])
        w_bias = strategy.get_weekly_bias(df_w)
        d_trend = strategy.get_daily_trend(df_d)
        h4_bias = strategy.get_4h_bias(df_4h)
        row_4h = df_4h.iloc[-1]
        rsi = round(float(row_4h["rsi"]), 1)
        adx = round(float(row_4h["adx"]), 1) if "adx" in row_4h else "N/A"
        h4_ema21 = round(float(row_4h["ema21"]), 5)

        swing = strategy.get_swing_range(df_4h, lookback=30)
        zones = strategy.find_aoi_zones(df_d, matched_sym)
        nearest = min(zones, key=lambda z: abs(z["mid"] - price)) if zones else None
        zone_pos = "Discount" if price < swing["equilibrium"] else "Premium"
        info = bridge.get_symbol_info(matched_sym) if hasattr(bridge, "get_symbol_info") else {}
        spread = info.get("spread", "N/A") if info else "N/A"

        tf_data = {
            "price": price, "w_bias": w_bias, "d_trend": d_trend,
            "d_ema200_side": "Above EMA200" if price > float(df_d["ema200"].iloc[-1]) else "Below EMA200",
            "h4_bias": h4_bias, "h4_ema21": h4_ema21, "adx": adx, "rsi": rsi,
            "swing_low": round(swing["low"], 5), "swing_high": round(swing["high"], 5),
            "equilibrium": round(swing["equilibrium"], 5), "zone_position": zone_pos,
            "nearest_aoi": round(nearest["mid"], 5) if nearest else "None",
            "spread": spread
        }
        report = ai_engine.analyze_market_pair(matched_sym, tf_data)
        notifier.send(report)
        # Store context turn in conversation memory
        ai_engine.save_chat_turn("user", f"Show institutional analysis for {matched_sym}")
        ai_engine.save_chat_turn("model", f"[Sent {matched_sym} institutional SMC market breakdown to trader]")
    except Exception as e:
        notifier.send(f"❌ Error analyzing {matched_sym}: {e}")


def _handle_ai_chat(query: str):
    """Processes conversational AI messages with typing indicator and memory."""
    notifier.send_typing()
    try:
        acc = bridge.get_account()
        positions = bridge.get_positions() or []
        recent_trades = trade_logger.get_recent_trades(limit=3) if hasattr(trade_logger, "get_recent_trades") else []
        status_info = {
            "account": config.ACCOUNTS.get(config.ACTIVE_ACCOUNT, {}).get("label", config.ACTIVE_ACCOUNT),
            "account_info": acc,
            "balance": acc.get("balance", 0.0) if acc else 0.0,
            "equity": acc.get("equity", 0.0) if acc else 0.0,
            "free_margin": acc.get("margin_free", 0.0) if acc else 0.0,
            "positions": positions,
            "open_trades": len(positions),
            "recent_trades": recent_trades,
            "daily_trades": trade_logger.get_daily_trade_count()
        }
        ans = ai_engine.chat_ai(query, status_info)
        notifier.send(ans)
    except Exception as e:
        notifier.send(f"❌ AI Chat Error: {e}")


def _handle_ai_command(item):
    text = item.get("text", "").strip()
    parts = text.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    if not arg or arg.upper() in ("HELP", "INFO"):
        notifier.send(
            "🧠 <b>FXAlexG Dual-AI Engine (Gemini + OpenRouter)</b>\n\n"
            "• <code>/ai [pair]</code> — Deep SMC institutional analysis (e.g. <code>/ai EURUSD</code>, <code>/ai BTC</code>, <code>/ai GOLD</code>)\n"
            "• <code>/ai pulse</code> — Macro session sentiment & trend flow\n"
            "• <code>/ai status</code> — Engine latency, active models & settings\n"
            "• <code>/ai [question]</code> — Interactive AI trading advisor\n"
            "• <code>/clear</code> — Clear chat conversation memory\n\n"
            "<i>Dual Engine: Google Gemini 2.5 Flash + OpenRouter DeepSeek</i>"
        )
        return

    upper_arg = arg.upper()
    if upper_arg == "STATUS":
        _handle_ai_status()
        return

    if upper_arg in ("PULSE", "MACRO", "SESSION"):
        _handle_ai_pulse()
        return

    # Strict symbol resolution - exact match only, never substring letters
    clean_sym = upper_arg.replace("/", "").replace(" ", "")
    SYMBOL_MAP = {
        "EURUSD": "EURUSDm", "EURUSDM": "EURUSDm", "EUR": "EURUSDm",
        "GBPUSD": "GBPUSDm", "GBPUSDM": "GBPUSDm", "GBP": "GBPUSDm",
        "USDJPY": "USDJPYm", "USDJPYM": "USDJPYm", "JPY": "USDJPYm",
        "USDCAD": "USDCADm", "USDCADM": "USDCADm", "CAD": "USDCADm",
        "AUDUSD": "AUDUSDm", "AUDUSDM": "AUDUSDm", "AUD": "AUDUSDm",
        "NZDUSD": "NZDUSDm", "NZDUSDM": "NZDUSDm", "NZD": "NZDUSDm",
        "USDCHF": "USDCHFm", "USDCHFM": "USDCHFm", "CHF": "USDCHFm",
        "EURJPY": "EURJPYm", "EURJPYM": "EURJPYm",
        "GBPJPY": "GBPJPYm", "GBPJPYM": "GBPJPYm",
        "XAUUSD": "XAUUSDm", "XAUUSDM": "XAUUSDm", "GOLD": "XAUUSDm", "XAU": "XAUUSDm",
        "BTCUSD": "BTCUSDm", "BTCUSDM": "BTCUSDm", "BTC": "BTCUSDm", "BITCOIN": "BTCUSDm",
        "ETHUSD": "ETHUSDm", "ETHUSDM": "ETHUSDm", "ETH": "ETHUSDm", "ETHEREUM": "ETHUSDm",
        "SOLUSD": "SOLUSDm", "SOLUSDM": "SOLUSDm", "SOL": "SOLUSDm", "SOLANA": "SOLUSDm",
    }

    if clean_sym in SYMBOL_MAP:
        _handle_ai_symbol_analysis(SYMBOL_MAP[clean_sym])
    else:
        _handle_ai_chat(arg)


def _handle_connect_account_step(item):
    chat_id = item.get("chat_id")
    text = item.get("text", "").strip()
    user_id = str(item.get("user_id", chat_id))
    user_name = item.get("user_name", "Trader")
    msg_id = item.get("message_id")
    lower_text = text.lower()
    
    if text in ("❌ Cancel Setup", "Cancel Setup") or lower_text in ("cancel", "/cancel"):
        if chat_id in ACCOUNT_WIZARDS:
            del ACCOUNT_WIZARDS[chat_id]
        notifier.send("❌ <b>Account setup cancelled.</b> Returning to main menu.", chat_id=chat_id, custom_keyboard=notifier.DEFAULT_KEYBOARD)
        return True

    state = ACCOUNT_WIZARDS.get(chat_id)
    if not state:
        return False
        
    step = state.get("step")
    
    # ── STEP 1: Broker Server Selection ──
    if step == "WAITING_SERVER":
        server_val = text.strip()
        state["server"] = server_val
        state["step"] = "WAITING_LOGIN"
        notifier.send(
            f"✅ <b>Broker Server Selected:</b> <code>{server_val}</code>\n\n"
            "🔢 <b>Step 2/4: Enter your MT5 Account Number (Login):</b>\n"
            "<i>(Type your numeric login, e.g. <code>436015341</code>)</i>",
            chat_id=chat_id,
            custom_keyboard=notifier.CANCEL_KEYBOARD
        )
        return True

    # ── STEP 2: Account Login ──
    if step == "WAITING_LOGIN":
        clean_num = text.replace("#", "").strip()
        try:
            login_num = int(clean_num)
            state["login"] = login_num
            state["step"] = "WAITING_PASSWORD"
            notifier.send(
                f"✅ <b>Login Saved:</b> <code>#{login_num}</code>\n\n"
                "🔑 <b>Step 3/4: Enter your MT5 Password:</b>\n"
                "<i>🔒 Security Notice: Your password message will be wiped from this chat immediately upon receipt.</i>",
                chat_id=chat_id,
                custom_keyboard=notifier.CANCEL_KEYBOARD
            )
        except ValueError:
            notifier.send("⚠️ <i>Please enter a valid numeric account number (e.g. 12345678):</i>", chat_id=chat_id, custom_keyboard=notifier.CANCEL_KEYBOARD)
        return True

    # ── STEP 3: Account Password ──
    if step == "WAITING_PASSWORD":
        state["password"] = text.strip()
        state["step"] = "WAITING_RISK"
        if msg_id:
            notifier.delete_message(chat_id, msg_id)
            
        notifier.send(
            "🔒 <b>Password Received & Cleared From Chat!</b>\n\n"
            "⚖️ <b>Step 4/4: Select Risk Profile:</b>\n"
            "Choose your desired risk per trade using the shortcut buttons below, or type a custom percentage (e.g. <code>1.0</code>):",
            chat_id=chat_id,
            custom_keyboard=notifier.RISK_SHORTCUTS_KEYBOARD
        )
        return True

    # ── STEP 4: Risk Profile & Live Broker Handshake ──
    if step == "WAITING_RISK":
        risk_val = 1.0
        if "0.5" in text:
            risk_val = 0.5
        elif "1.0" in text:
            risk_val = 1.0
        elif "2.0" in text:
            risk_val = 2.0
        else:
            try:
                risk_val = float(text.replace("%", "").strip())
            except ValueError:
                risk_val = 1.0
        
        login_val = state["login"]
        server_val = state["server"]
        pw_val = state["password"]
        
        notifier.send("🔄 <b>Connecting to MT5 Broker & Verifying Credentials...</b>\n<i>Please wait ~2-3 seconds...</i>", chat_id=chat_id)
        
        test_res = bridge.switch_account(login_val, pw_val, server_val)
        if test_res and "error" not in test_res:
            acc_info = bridge.get_account() or test_res
            bal = float(acc_info.get("balance", 0.0))
            eq = float(acc_info.get("equity", bal))
            cur = str(acc_info.get("currency", "USD"))
            
            # Store in encrypted SQLite database
            account_manager.add_or_update_account(
                login=login_val,
                server=server_val,
                password=pw_val,
                label=f"{user_name}'s Account",
                user_id=user_id,
                chat_id=str(chat_id),
                risk_pct=risk_val,
                balance=bal,
                equity=eq,
                currency=cur
            )
            
            if chat_id in ACCOUNT_WIZARDS:
                del ACCOUNT_WIZARDS[chat_id]
            
            # Restore bridge connection to active monitoring profile
            curr_active = load_active_account()
            prof = config.ACCOUNTS.get(curr_active, {})
            if prof:
                bridge.switch_account(prof.get("login"), prof.get("password"), prof.get("server"))
                
            notifier.send(
                f"🎉 <b>Account Connected Successfully!</b>\n\n"
                f"👤 <b>Account:</b> <code>#{login_val}</code>\n"
                f"🏦 <b>Broker Server:</b> {server_val}\n"
                f"💰 <b>Balance:</b> <b>${bal:.2f} {cur}</b> | Equity: ${eq:.2f}\n"
                f"🛡️ <b>Risk Profile:</b> <b>{risk_val}% per trade</b>\n"
                f"⚡ <b>Auto-Copy Status:</b> <b>ACTIVE ✅</b>\n\n"
                f"<i>This account is now registered to automatically copy institutional A+ trade setups in real time.</i>",
                chat_id=chat_id,
                custom_keyboard=notifier.DEFAULT_KEYBOARD
            )
        else:
            err = test_res.get("error", "Invalid login or broker unreachable") if test_res else "Bridge connection timeout"
            if chat_id in ACCOUNT_WIZARDS:
                del ACCOUNT_WIZARDS[chat_id]
            curr_active = load_active_account()
            prof = config.ACCOUNTS.get(curr_active, {})
            if prof:
                bridge.switch_account(prof.get("login"), prof.get("password"), prof.get("server"))
                
            notifier.send(
                f"❌ <b>Broker Verification Failed</b>\n\n"
                f"Reason: <code>{err}</code>\n\n"
                f"Please ensure your MT5 login, password, and server name (e.g. <code>Exness-MT5Trial10</code>) are exact and try again.",
                chat_id=chat_id,
                custom_keyboard=notifier.DEFAULT_KEYBOARD
            )
        return True

    return False


def _handle_show_accounts(chat_id, user_id=None):
    accounts = account_manager.get_all_accounts()
    if not accounts:
        notifier.send("ℹ️ No accounts registered yet. Tap <b>➕ Connect Account</b> to link one!", chat_id=chat_id)
        return

    lines = [f"👥 <b>Your {len(accounts)} trading bots</b>\n"]
    for a in accounts:
        status = "🟢" if a["is_active"] else "⏸️ PAUSED"
        lines.append(
            f"{status} <b>{notifier.bot_tag(a)}</b>\n"
            f"   Balance ${a.get('balance', 0.0):.2f} · Risk {a.get('risk_pct', 1.0)}% · {a['server']}\n"
        )
    lines.append(
        "🏆 /scoreboard — who is winning\n"
        "<code>/strategy [login] [trend|trend_fast|smc|smc_ai]</code> · <code>/risk [login] [pct]</code>\n"
        "<code>/pause [login]</code> · <code>/resume [login]</code> · <code>/remove [login]</code>"
    )
    notifier.send("\n".join(lines), chat_id=chat_id)


def _handle_scoreboard(chat_id=None):
    """Refreshes each account's live balance, then ranks the bots by profit."""
    notifier.send_typing(chat_id)
    accounts = account_manager.get_active_accounts()
    master = _master_login()
    try:
        for a in accounts:
            login = int(a["login"])
            if login != master:
                sw = bridge.switch_account(login, a["password"], a["server"])
                if not sw or "error" in sw:
                    continue
            info = bridge.get_account()
            if info and int(info.get("login", 0)) == login:
                account_manager.update_account_balance(login, info.get("balance", 0.0), info.get("equity", 0.0), info.get("currency", "USD"))
    finally:
        _restore_master()

    stats = trade_logger.stats_by_login()
    rows = []
    for a in account_manager.get_active_accounts():
        st = stats.get(int(a["login"]), {})
        rows.append((st.get("net", 0.0), a, st))
    rows.sort(key=lambda r: r[0], reverse=True)

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
    lines = ["🏆 <b>SCOREBOARD</b> — bots ranked by closed-trade profit\n"]
    for i, (net, a, st) in enumerate(rows):
        closed = st.get("closed", 0)
        wr = f"{st['wins'] / closed * 100:.0f}%" if closed else "–"
        sign = "+" if net >= 0 else "-"
        lines.append(
            f"{medals[i] if i < len(medals) else '•'} <b>{notifier.bot_tag(a)}</b>\n"
            f"   P&L <b>{sign}${abs(net):.2f}</b> · {closed} closed ({st.get('wins', 0)}W/{st.get('losses', 0)}L, {wr}) · {st.get('open', 0)} open\n"
            f"   Balance <b>${a.get('balance', 0.0):.2f}</b> · best +${st.get('best', 0.0):.2f} · worst -${abs(st.get('worst', 0.0)):.2f}\n"
        )
    lines.append(f"<i>Updated {_ts()}</i>")
    notifier.send("\n".join(lines), chat_id=chat_id)


def _handle_account_quick_command(item):
    cmd = item.get("command", "").lower()
    text = item.get("text", "").strip()
    chat_id = item.get("chat_id")
    parts = text.split()
    
    if cmd in ("/pause", "/stop") and len(parts) >= 2:
        try:
            target_login = int(parts[1].replace("#", ""))
            account_manager.toggle_account_status(target_login, active=False)
            notifier.send(f"⏸️ <b>Account #{target_login} Paused.</b> Signal execution temporarily suspended.", chat_id=chat_id)
            return True
        except ValueError:
            pass

    if cmd in ("/resume", "/play", "/startacc") and len(parts) >= 2:
        try:
            target_login = int(parts[1].replace("#", ""))
            account_manager.toggle_account_status(target_login, active=True)
            notifier.send(f"▶️ <b>Account #{target_login} Resumed.</b> Signal execution is now active.", chat_id=chat_id)
            return True
        except ValueError:
            pass

    if cmd in ("/remove", "/delete", "/disconnect") and len(parts) >= 2:
        try:
            target_login = int(parts[1].replace("#", ""))
            account_manager.delete_account(target_login)
            notifier.send(f"🗑️ <b>Account #{target_login} Disconnected.</b> Removed from trading registry.", chat_id=chat_id)
            return True
        except ValueError:
            pass

    if cmd == "/strategy" and len(parts) >= 3:
        try:
            target_login = int(parts[1].replace("#", ""))
            strat = parts[2].lower()
            if account_manager.set_account_strategy(target_login, strat):
                notifier.send(f"🧭 <b>Account #{target_login} now trades:</b> {strat}", chat_id=chat_id)
            else:
                notifier.send(f"⚠️ Unknown account or strategy. Use one of: {', '.join(account_manager.STRATEGIES)}", chat_id=chat_id)
            return True
        except ValueError:
            pass

    if cmd in ("/risk", "/setrisk") and len(parts) >= 3:
        try:
            target_login = int(parts[1].replace("#", ""))
            new_risk = float(parts[2].replace("%", ""))
            account_manager.set_account_risk(target_login, new_risk)
            notifier.send(f"⚖️ <b>Risk updated for Account #{target_login}:</b> {new_risk}% per trade.", chat_id=chat_id)
            return True
        except ValueError:
            pass
            
    return False


def handle_telegram_commands():
    commands = notifier.get_commands()
    for item in commands:
        text = item.get("text", "").strip()
        cmd = item.get("command", "").lower()
        chat_id = item.get("chat_id")
        user_id = item.get("user_id")
        lower_text = text.lower()

        # A. Check if user is currently inside the interactive Connect Account wizard
        if chat_id in ACCOUNT_WIZARDS or text in ("❌ Cancel Setup", "Cancel Setup") or lower_text in ("cancel", "/cancel"):
            if _handle_connect_account_step(item):
                continue

        # B. Exact Keyboard Button Matching & Direct Short Commands
        if text in ("➕ Connect Account", "Connect Account") or cmd in ("/connect", "/addaccount", "/add_account"):
            ACCOUNT_WIZARDS[chat_id] = {
                "step": "WAITING_SERVER",
                "user_id": user_id,
                "user_name": item.get("user_name", "Trader")
            }
            notifier.send(
                "🏦 <b>Connect Your MT5 Trading Account (Step 1/4)</b>\n\n"
                "Tap your broker server below or type your broker server name (e.g. <code>Exness-MT5Trial10</code> or <code>ICMarketsSC-Live</code>):",
                chat_id=chat_id,
                custom_keyboard=notifier.SERVER_SHORTCUTS_KEYBOARD
            )
        elif text in ("🏆 Scoreboard", "Scoreboard") or cmd in ("/scoreboard", "/score", "/winner") or lower_text == "scoreboard":
            _handle_scoreboard(chat_id)
        elif text in ("👥 My Accounts", "My Accounts") or cmd in ("/accounts", "/myaccounts"):
            _handle_show_accounts(chat_id, user_id)
        elif _handle_account_quick_command(item):
            continue
        elif text in ("🧬 Agent Evolution", "Agent Evolution") or cmd in ("/evolve", "/train", "/generation"):
            _handle_agent_evolution()
        elif text == "📊 Market Pulse" or cmd in ("/pulse", "/macro") or lower_text in ("pulse", "market pulse"):
            _handle_ai_pulse()
        elif text == "🏦 Balance & Status" or cmd in ("/account", "/balance", "/status") or lower_text in ("balance", "status"):
            _show_account_info()
        elif text == "🥇 Gold" or cmd == "/gold" or lower_text in ("gold", "/gold"):
            _handle_ai_symbol_analysis("XAUUSDm")
        elif text == "💶 EUR/USD" or cmd == "/eurusd" or lower_text in ("eur/usd", "eurusd", "/eurusd"):
            _handle_ai_symbol_analysis("EURUSDm")
        elif text == "₿ Bitcoin" or cmd in ("/btc", "/bitcoin") or lower_text in ("bitcoin", "btc", "/btc"):
            _handle_ai_symbol_analysis("BTCUSDm")
        elif text == "📈 P&L Report" or cmd in ("/report", "/pnl") or lower_text in ("report", "pnl", "p&l report"):
            _send_report()
        elif text == "📰 Today's News" or cmd == "/news" or lower_text in ("news", "today's news"):
            news_msg = news_filter.get_todays_news()
            notifier.send(news_msg)
        elif text in ("⚙️ Help & Menu", "Help & Menu", "⚙️ Help & Shortcuts") or cmd in ("/start", "/help") or lower_text in ("help", "menu", "start"):
            _send_welcome_menu(chat_id=chat_id)
        elif cmd in ("/clear", "/reset") or lower_text in ("clear memory", "reset memory", "clear"):
            ai_engine.clear_chat_history()
            notifier.send("🧹 <b>AI Conversation Memory Cleared!</b> Ready for a fresh start.", chat_id=chat_id)
        elif cmd.startswith("/ai"):
            _handle_ai_command(item)
        else:
            # Freeform natural language query to AI Brain
            _handle_ai_chat(text)


def _switch_account(target):
    profile = config.ACCOUNTS.get(target)
    if not profile:
        notifier.send(f"❌ Unknown account profile: {target}")
        return

    positions = bridge.get_positions()
    if positions:
        notifier.send(
            f"⚠️ <b>Cannot switch accounts</b> — {len(positions)} open position(s) active.\n"
            f"Close all trades first, then switch."
        )
        return

    notifier.send(f"🔄 Switching to <b>{profile['label']}</b>...")
    print(f"[{_ts()}] Switching account to {target} ({profile['server']})...")

    result = bridge.switch_account(
        login=profile["login"],
        password=profile["password"],
        server=profile["server"],
    )

    if result and "error" not in result:
        save_active_account(target)
        bal = result.get("balance", 0.0)
        srv = result.get("server", "?")
        lev = result.get("leverage", "?")
        msg = (
            f"✅ <b>Successfully Switched to {profile['label']}</b>\n\n"
            f"Login:    <code>{result.get('login', '?')}</code>\n"
            f"Server:   {srv}\n"
            f"Balance:  <b>${bal:.2f} {result.get('currency', 'USD')}</b>\n"
            f"Leverage: 1:{lev}"
        )
        notifier.send(msg)
        print(f"[{_ts()}] Switched to {target} — Balance: ${bal:.2f}")
    else:
        err = result.get("error", "unknown") if result else "no response"
        notifier.send(f"❌ <b>Account switch failed:</b> {err}")
        print(f"[{_ts()}] Account switch failed: {err}")



def _send_report():
    today = bridge.get_report("today")
    week = bridge.get_report("week")
    
    if "error" in today or "error" in week:
        notifier.send("⚠️ <b>Report Failed:</b> Could not fetch deals from bridge.")
        return

    msg = (
        f"📈 <b>Performance Report</b>\n\n"
        f"<b>TODAY:</b>\n"
        f"Profit: ${today.get('profit', 0.0):.2f}\n"
        f"Trades: {today.get('total_trades', 0)} ({today.get('wins', 0)}W - {today.get('losses', 0)}L)\n"
        f"Win Rate: {today.get('winrate', 0.0)}%\n\n"
        f"<b>THIS WEEK:</b>\n"
        f"Profit: ${week.get('profit', 0.0):.2f}\n"
        f"Trades: {week.get('total_trades', 0)} ({week.get('wins', 0)}W - {week.get('losses', 0)}L)\n"
        f"Win Rate: {week.get('winrate', 0.0)}%"
    )
    notifier.send(msg)


def _show_account_info():
    acc = bridge.get_account()
    target = load_active_account()
    profile = config.ACCOUNTS.get(target, {})
    label = profile.get("label", target)
    positions = bridge.get_positions()
    if acc and acc.get("connected"):
        algo_active = acc.get("trade_allowed", True)
        algo_badge = "ACTIVE ✅" if algo_active else "DISABLED ❌ (Check MT5 Algo Trading!)"
        notifier.send(
            f"🏦 <b>Account Information</b>\n\n"
            f"Profile:     <b>{label}</b>\n"
            f"Login:       <code>{acc.get('login', '?')}</code>\n"
            f"Server:      {acc.get('server', '?')}\n"
            f"AutoTrading: <b>{algo_badge}</b>\n"
            f"Balance:     <b>${acc.get('balance', 0.0):.2f} {acc.get('currency', 'USD')}</b>\n"
            f"Equity:      <b>${acc.get('equity', 0.0):.2f}</b>\n"
            f"Leverage:    1:{acc.get('leverage', '?')}\n"
            f"Open Trades: <b>{len(positions) if positions else 0}</b>\n\n"
            f"Quick Switch: /real | /demo"
        )
    else:
        notifier.send("⚠️ MT5 Bridge is currently offline or reconnecting.")


def main():
    print("=" * 60)
    print("  FXAlexG Trading Bot v3.1 — Institutional Strategy Edition")
    print("=" * 60)

    trade_logger.init_db()
    active_acc = load_active_account()
    print(f"[{_ts()}] Active Account Profile: {active_acc}")

    if not notifier._load_chat_id():
        print("⏳ Waiting for Telegram /start to @anonye_trading_bot...")
        while not notifier._load_chat_id():
            notifier.discover_chat_id()
            time.sleep(5)
    print("✅ Telegram bot connected.")

    acc = bridge.get_account()
    if acc and acc.get("connected"):
        bal = acc.get("balance", 0.0)
        print(f"✅ MT5 Bridge online — Account: {acc.get('login')} | Balance: ${bal:.2f}")
        notifier.send_startup(bal, config.SYMBOLS)
    else:
        print(f"⚠️ MT5 Bridge not fully connected at {config.BRIDGE_URL}")
        notifier.send(
            f"🤖 <b>FXAlexG Bot v3.1 Started</b>\n\n"
            f"Active Profile: <b>{active_acc.upper()}</b>\n"
            f"Pairs: {', '.join(config.SYMBOLS)}\n"
            f"Risk: {config.RISK_PERCENT}% per trade | Max/Day: {config.MAX_TRADES_DAY}\n"
            f"Regime: ADX > {config.ADX_MIN} | Cooldown: {config.COOLDOWN_HOURS}h post-loss\n"
            f"TP1: 1:{config.TP1_RR} (50% partial) | TP2: 1:{config.TP2_RR} (trailing)\n"
            f"Commands: /real /demo /account /news /help"
        )

    last_scan_slot  = (-1, -1)
    last_trend_slot = None
    last_other_check = 0.0
    last_ema_check  = 0.0
    weekly_report_sent = False
    last_tp_check   = 0.0
    last_cmd_check  = 0.0

    while True:
        try:
            now = datetime.now(timezone.utc)
            h, m = now.hour, now.minute

            check_closed_positions()

            # Poll Telegram commands every 5s
            if time.time() - last_cmd_check >= 5:
                handle_telegram_commands()
                last_cmd_check = time.time()

            # Manage open positions (TP1, Trailing SL, TTL) every 15s
            if time.time() - last_tp_check >= 15:
                manage_open_positions()
                last_tp_check = time.time()

            # Early exit guard disabled: Trades are governed strictly by hard MT5 SL/TP to prevent premature noise exits

            # Weekend analysis
            if (now.weekday() == 5 and now.hour == config.WEEKEND_ANALYSIS_HOUR
                    and not weekly_analyst.already_ran_this_week()):
                try:
                    weekly_analyst.run_all(config.SYMBOLS)
                except Exception as e:
                    print(f"[{_ts()}] Weekend analysis error: {e}")

            # Daily Auto News Broadcast (06:00 UTC)
            today_date = now.strftime("%Y-%m-%d")
            if now.hour == 6 and getattr(config, 'last_news_date', '') != today_date:
                try:
                    news_msg = news_filter.get_todays_news()
                    notifier.send(news_msg)
                    config.last_news_date = today_date
                except Exception as e:
                    print(f"[{_ts()}] Auto news error: {e}")

            # Which methods are in use is decided by the accounts (accounts.strategy).
            in_use = {(a.get("strategy") or "trend") for a in account_manager.get_active_accounts()} \
                or {getattr(config, "STRATEGY", "trend")}

            # Extra accounts: reconcile closes + manage stops every 5 minutes
            if time.time() - last_other_check >= 300:
                manage_other_accounts()
                last_other_check = time.time()

            if in_use & set(trend_strategy.VARIANTS):
                # Daily trend strategy: once an hour (5 min past) — entries fire once per new daily
                # candle, stops are trailed on the latest daily close.
                hour_slot = (now.strftime("%Y-%m-%d"), h)
                if m >= 5 and hour_slot != last_trend_slot:
                    print(f"\n[{_ts()}] ── TREND CHECK ──")
                    manage_trend_positions()
                    run_trend_scan()
                    last_trend_slot = hour_slot

            if in_use & {"smc", "smc_ai"}:
                # Kill Zone & 4H Candle Close Scans
                kz   = current_kill_zone()
                slot = (h, (m // 15) * 15)

                should_scan = False
                if kz:
                    if slot != last_scan_slot:
                        should_scan = True
                else:
                    if h in config.SCAN_HOURS and m == 0 and slot != last_scan_slot:
                        should_scan = True

                if should_scan:
                    label = f"KILL ZONE ({kz.upper()})" if kz else "4H CLOSE"
                    print(f"\n[{_ts()}] ── {label} — SMC scanning ──")
                    run_scan(kill_zone=kz)
                    last_scan_slot = slot

            time.sleep(15)

        except KeyboardInterrupt:
            print("\nStopped.")
            notifier.send("🛑 FXAlexG Bot stopped.")
            break
        except Exception as e:
            print(f"[{_ts()}] LOOP ERROR: {e}")
            notifier.send_error(str(e))
            time.sleep(30)


if __name__ == "__main__":
    main()
