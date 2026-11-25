"""
Momentum Breakout bot on GitHub Actions — no VPS. The trading half; telegram_control.py is the Telegram half.

Each run (hourly, or right away when you ask in Telegram) a GitHub Windows machine installs MetaTrader 5,
logs in, and runs this once:
  1. answer queued Telegram requests (/status /trades /close /closeall /run)
  2. manage open momentum trades (raise trailing stops; close daily-engine trades when the BTC filter turns off)
  3. report trades that closed since the last run
  4. buy new breakouts (same momentum_strategy.py rules as the VPS bot) unless you sent /pause
  5. save a private run log (/log), health (/health) and the filter (/filter); daily summary at 07:00 UTC

Public repo => public Actions logs: this script prints no account numbers, balances, prices or trades.
Details go to Telegram and into the encrypted state (cloud_common.py).

MT5 login: /login in Telegram (stored encrypted), or GitHub secrets MT5_LOGIN / MT5_PASSWORD / MT5_SERVER.
Env: MT5_PATH, MT5_PORTABLE, MOMENTUM_TELEGRAM_TOKEN, MOMENTUM_TELEGRAM_CHAT_ID, MOMENTUM_RISK_PCT, MODE=live|dry
"""

import math
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config
import momentum_strategy as M
from cloud_common import CONTROL_STATE, DEFAULT_SERVER, RUNNER_STATE, load_json, save_json, tg_send

MAGIC = 26112501
COMMENT = {"mom_4h": "MOM 4H", "mom_d1": "MOM D1"}


def log(msg):
    """Public log line: never pass account data, prices or balances here."""
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ── MetaTrader 5 ───────────────────────────────────────────────────────────────

class Broker:
    def __init__(self, mt5):
        self.mt5 = mt5
        self.offset = 0          # trade-server time minus UTC, in hours
        self.names = {}

    def connect(self, path, login, password, server, portable=False):
        kw = {"login": int(login), "password": password, "server": server, "timeout": 180000}
        if path:
            kw["path"] = path
        if portable:
            kw["portable"] = True      # settings (incl. Algo Trading on) live next to terminal64.exe
        if not self.mt5.initialize(**kw):
            return False, str(self.mt5.last_error())
        return True, ""

    def calibrate(self, symbol, now):
        """Trade-server clock minus UTC, whole hours (Exness is UTC+0, but don't assume it)."""
        tick = self.mt5.symbol_info_tick(symbol)
        if tick and tick.time:
            off = round((tick.time - now.timestamp()) / 3600)
            self.offset = off if abs(off) <= 14 else 0      # a stale tick (weekend/holiday) can't be an offset

    def resolve(self, name):
        """Exness standard accounts use an 'm' suffix (SOLUSDm); try with and without it."""
        if name in self.names:
            return self.names[name]
        for cand in (name, name[:-1] if name.endswith("m") else name + "m"):
            if self.mt5.symbol_info(cand) is not None:
                self.mt5.symbol_select(cand, True)
                self.names[name] = cand
                return cand
        self.names[name] = None
        return None

    def bars(self, symbol, engine, n=300):
        tf = self.mt5.TIMEFRAME_H4 if engine == "mom_4h" else self.mt5.TIMEFRAME_D1
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, n)
        if rates is None or len(rates) == 0:
            return None
        df = pd.DataFrame(rates)
        df.index = pd.to_datetime(df["time"] - self.offset * 3600, unit="s", utc=True)
        return df[["open", "high", "low", "close"]].astype(float)

    def utc(self, server_ts):
        return pd.Timestamp(server_ts - self.offset * 3600, unit="s", tz="UTC")

    def positions(self):
        return [p for p in (self.mt5.positions_get() or []) if p.magic == MAGIC]

    def deals(self, since_utc):
        frm = datetime.fromtimestamp(since_utc.timestamp() + self.offset * 3600, tz=timezone.utc)
        to = datetime.now(timezone.utc) + timedelta(days=3650)      # "until now", whatever the clocks say
        return [d for d in (self.mt5.history_deals_get(frm, to) or []) if d.magic == MAGIC]

    def _filling(self, info):
        fm = getattr(info, "filling_mode", 0) or 0
        if fm & 1:
            return self.mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, req):
        res = self.mt5.order_send(req)
        ok = res is not None and res.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED)
        return ok, (res.retcode if res is not None else None), (getattr(res, "comment", "") if res is not None else
                                                                 str(self.mt5.last_error()))

    def buy(self, symbol, lot, sl, comment):
        info, tick = self.mt5.symbol_info(symbol), self.mt5.symbol_info_tick(symbol)
        return self._send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
                           "type": self.mt5.ORDER_TYPE_BUY, "price": tick.ask, "sl": round(sl, info.digits),
                           "tp": 0.0, "deviation": 50, "magic": MAGIC, "comment": comment,
                           "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)})

    def modify_sl(self, pos, sl):
        info = self.mt5.symbol_info(pos.symbol)
        return self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": pos.ticket,
                           "sl": round(sl, info.digits), "tp": pos.tp, "magic": MAGIC})

    def close(self, pos):
        info, tick = self.mt5.symbol_info(pos.symbol), self.mt5.symbol_info_tick(pos.symbol)
        return self._send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
                           "type": self.mt5.ORDER_TYPE_SELL, "position": pos.ticket, "price": tick.bid,
                           "deviation": 50, "magic": MAGIC, "comment": "MOM exit",
                           "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)})


def size_trade(info, sl_dist, balance, risk_pct, cap_pct):
    """Lot for risk_pct of balance; the broker minimum is allowed only if it stays within cap_pct.
    Returns (lot, risk_usd, reason_if_skipped)."""
    loss_per_lot = sl_dist / info.trade_tick_size * info.trade_tick_value
    if loss_per_lot <= 0:
        return None, 0.0, "invalid tick value"
    step = info.volume_step or 0.01
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    lot = math.floor(balance * risk_pct / 100 / loss_per_lot / step + 1e-9) * step
    if lot < info.volume_min:
        min_risk = info.volume_min * loss_per_lot
        if min_risk <= balance * cap_pct / 100:
            lot = info.volume_min
        else:
            return None, min_risk, (f"minimum lot {info.volume_min} would risk ${min_risk:.2f} "
                                    f"({min_risk / balance * 100:.0f}% of balance), cap is {cap_pct:.0f}%")
    lot = round(min(lot, info.volume_max), dec)
    return lot, lot * loss_per_lot, None


# ── one run ────────────────────────────────────────────────────────────────────

def engine_of(pos):
    c = (pos.comment or "").upper()
    return "mom_d1" if "D1" in c else "mom_4h"


def filter_text(btc_d, now):
    closed = M.closed_bars(btc_d, 24, now)
    c = closed["close"]
    e = c.ewm(span=M.REGIME_EMA, adjust=False).mean()
    last, ema = float(c.iloc[-1]), float(e.iloc[-1])
    on = last > ema
    txt = (f"🛰️ <b>Market filter {'ON ✅' if on else 'OFF ⏸️'}</b>\n"
           f"BTC close {closed.index[-1]:%d %b}: <b>{last:,.0f}</b> · EMA{M.REGIME_EMA}: <b>{ema:,.0f}</b> "
           f"({(last / ema - 1) * 100:+.1f}%)\n")
    txt += ("Breakouts are being traded." if on else
            f"No new buys until BTC closes above ~{ema:,.0f} (+{(ema / last - 1) * 100:.1f}%).")
    return txt


def run(mt5, env, state, control=None, http=requests, now=None):
    """state: this side's encrypted state (written back by main). control: the Telegram listener's state
    (read-only here). Returns the process exit code."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    control = control or {}
    live = env.get("MODE", "live") == "live"
    token = env.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    chat = str(env.get("MOMENTUM_TELEGRAM_CHAT_ID") or control.get("tg_chat") or "") or None
    notify = (lambda t: tg_send(http, token, chat, t)) if live else (lambda _t: False)
    plog = []                                                   # private log -> /log in Telegram
    def note(line):
        plog.append(f"{now:%H:%M} {line}")
    health = dict(state.get("health") or {})
    events = []
    done = list(state.get("done") or [])
    requests_ = [r for r in control.get("requests", []) if r["id"] not in set(done)] if live else []

    def finish(code, result):
        state.update(last_run=now.timestamp(), result=result, log=plog[-60:], health=health, done=done[-200:])
        return code

    creds = control.get("creds") or {}
    login = env.get("MT5_LOGIN") or creds.get("login")
    password = env.get("MT5_PASSWORD") or creds.get("password")
    server = (env.get("MT5_SERVER") if env.get("MT5_LOGIN") else creds.get("server")) or DEFAULT_SERVER
    if not login or not password:
        log("No MT5 login yet.")
        note("No MT5 login yet — send /login ACCOUNT PASSWORD SERVER")
        health.update(login="not set")
        if live and not state.get("asked_login") and notify(
                "🛰️ I need your MT5 login to trade. Send:\n<code>/login 12345678 YourPassword Exness-MT5Trial9</code>"):
            state["asked_login"] = True
        return finish(0, "waiting for /login")

    br = Broker(mt5)
    ok, err = br.connect(env.get("MT5_PATH"), login, password, server, portable=env.get("MT5_PORTABLE") == "1")
    if not ok:
        log("MT5 login failed.")
        note(f"MT5 login failed for account {login} on {server}: {err}")
        health.update(login=f"FAILED ({err})", error=f"login failed: {err}")
        if live and (requests_ or now.timestamp() - state.get("login_alert", 0) > 6 * 3600):
            notify(f"⚠️ <b>MT5 login failed</b> ({err}). Send /login again with the right account, password and server.")
            state["login_alert"] = now.timestamp()
        done += [r["id"] for r in requests_]
        return finish(1, "login failed")
    state.pop("login_alert", None)
    state.pop("asked_login", None)
    log("MT5 connected.")
    note(f"Logged in to {login} on {server}")
    first_login = health.get("login") != "ok"
    health.update(login="ok", error=None)

    term = mt5.terminal_info()
    health["algo"] = "on" if (term is None or term.trade_allowed) else "OFF — orders may be rejected"
    if live and term is not None and not term.trade_allowed and not state.get("algo_alert"):
        notify("⚠️ MT5 reports Algo Trading is disabled; orders may be rejected.")
        state["algo_alert"] = True

    syms = {s: br.resolve(s) for s in getattr(config, "MOMENTUM_SYMBOLS", [])}
    missing = [s for s, v in syms.items() if not v]
    if missing:
        note(f"Not on this account: {', '.join(missing)}")
    btc = br.resolve(getattr(config, "MOMENTUM_REGIME_SYMBOL", "BTCUSDm"))
    if not btc:
        log("BTC symbol not available — stopping.")
        note("BTC symbol not available on this account — can't read the market filter")
        health["error"] = "BTC symbol missing"
        return finish(1, "BTC missing")
    br.calibrate(btc, now)
    btc_d = br.bars(btc, "mom_d1")
    on = M.regime_ok(btc_d, now)
    closed_btc = M.closed_bars(btc_d, 24, now)
    ema = float(closed_btc["close"].ewm(span=M.REGIME_EMA, adjust=False).mean().iloc[-1])
    health["filter"] = {"on": bool(on), "btc": float(closed_btc["close"].iloc[-1]), "ema": ema}
    log(f"Market filter {'ON' if on else 'OFF'}.")
    note(f"BTC filter {'ON' if on else 'OFF'}: BTC {health['filter']['btc']:,.0f} vs EMA{M.REGIME_EMA} {ema:,.0f}")
    if state.get("regime") is not None and state["regime"] != on:
        events.append(f"🛰️ <b>Market filter {'ON' if on else 'OFF'}</b> — BTC closed "
                      f"{'above' if on else 'below'} its {M.REGIME_EMA}-day EMA.")
    state["regime"] = bool(on)

    acct = mt5.account_info()
    balance = float(acct.balance) if acct else 0.0
    health["balance"] = f"${balance:.2f}"

    def close_pos(pos, why):
        ok_, code, msg = br.close(pos) if live else (True, None, "")
        note(f"Close {pos.symbol} ({why}): {'done' if ok_ else f'FAILED {code} {msg}'}")
        return ok_, code, msg

    # 1. queued Telegram requests that change positions
    for r in requests_:
        if r["cmd"] == "close":
            want = r["arg"].upper().rstrip("M")
            hits = [p for p in br.positions() if p.symbol.upper().rstrip("M") == want]
            if not hits:
                notify(f"🛰️ No open momentum trade on {r['arg']}.")
            for p in hits:
                ok_, code, msg = close_pos(p, "you asked")
                notify(f"{'✅ Closed' if ok_ else '❌ Could not close'} {p.symbol}" + ("" if ok_ else f" ({code} {msg})"))
        elif r["cmd"] == "closeall":
            ps = br.positions()
            res = [close_pos(p, "close all")[0] for p in ps]
            notify(f"✅ Closed {sum(res)} of {len(ps)} momentum trades." if ps else "🛰️ No open momentum trades.")

    # 2. manage open trades
    for pos in br.positions():
        eng = engine_of(pos)
        df = br.bars(pos.symbol, eng)
        action, new_sl = M.manage(df, eng, br.utc(pos.time), pos.sl, on, now)
        if action == "exit":
            ok_, code, msg = close_pos(pos, "BTC filter off")
            events.append(f"🛰️ {'Closed' if ok_ else '⚠️ Could not close'} {pos.symbol} — market filter off"
                          + ("" if ok_ else f" ({code} {msg})"))
        elif action == "trail":
            tick, info = mt5.symbol_info_tick(pos.symbol), mt5.symbol_info(pos.symbol)
            min_gap = (getattr(info, "trade_stops_level", 0) or 0) * info.point
            if new_sl > (pos.sl or 0) + info.point and new_sl < tick.bid - min_gap:
                if live:
                    ok_, code, msg = br.modify_sl(pos, new_sl)
                    note(f"{pos.symbol}: stop {pos.sl:.6g} -> {new_sl:.6g} {'ok' if ok_ else f'FAILED {code} {msg}'}")
                    if ok_:
                        events.append(f"🛰️ {pos.symbol}: stop raised to {new_sl:.6g}")
        else:
            note(f"{pos.symbol}: holding, stop {pos.sl:.6g}, P&L ${pos.profit:+.2f}")

    # 3. trades closed since the last run
    since = pd.Timestamp(state["last_run"], unit="s", tz="UTC") if state.get("last_run") else now - pd.Timedelta(hours=2)
    for d in br.deals(since):
        if d.entry == mt5.DEAL_ENTRY_OUT:
            res = d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
            events.append(f"🏁 <b>Closed {d.symbol}</b> {'✅' if res > 0 else '❌'} <b>${res:+.2f}</b>")
            note(f"Closed {d.symbol}: ${res:+.2f}")

    # 4. new entries
    entries = 0
    risk = float(control.get("risk_pct") or env.get("MOMENTUM_RISK_PCT") or getattr(config, "MOMENTUM_RISK_PCT", 5.0))
    if control.get("paused"):
        note("Paused by you (/resume to allow new trades)")
    elif not on:
        note("No new buys: BTC filter is off")
    else:
        cap = getattr(config, "MOMENTUM_MAX_RISK_PCT", 8.0)
        max_open = getattr(config, "MOMENTUM_MAX_OPEN", 4)
        for eng in getattr(config, "MOMENTUM_ENGINES", ["mom_4h", "mom_d1"]):
            label = M.ENGINES[eng]["label"]
            for name, sym in syms.items():
                open_pos = br.positions()
                if not sym or any(p.symbol == sym for p in open_pos):
                    continue
                if len(open_pos) >= max_open:
                    note(f"Max {max_open} open trades reached")
                    break
                setup = M.entry_signal(sym, br.bars(sym, eng), eng, on, now)
                if not setup:
                    note(f"{sym} {label}: no breakout")
                    continue
                candle_close = pd.Timestamp(setup["signal_time"]) + pd.Timedelta(hours=M.ENGINES[eng]["tf_hours"])
                if any(d.symbol == sym and d.entry == mt5.DEAL_ENTRY_IN for d in br.deals(candle_close)):
                    note(f"{sym} {label}: breakout already traded")
                    continue
                info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
                if info.spread * info.point > getattr(config, "MOMENTUM_MAX_SPREAD_FRAC", 0.15) * setup["sl_dist"]:
                    note(f"{sym} {label}: breakout skipped, spread too wide")
                    events.append(f"🛰️ {sym} breakout skipped: spread too wide for the stop")
                    continue
                lot, risk_usd, why = size_trade(info, setup["sl_dist"], balance, risk, cap)
                if lot is None:
                    note(f"{sym} {label}: breakout skipped, {why}")
                    events.append(f"🛰️ {sym} breakout skipped: {why}")
                    continue
                sl = tick.ask - setup["sl_dist"]
                if not live:
                    entries += 1
                    continue
                ok_, code, msg = br.buy(sym, lot, sl, COMMENT[eng])
                note(f"BUY {sym} {lot} lot, stop {sl:.6g}, risk ${risk_usd:.2f}: {'ok' if ok_ else f'REJECTED {code} {msg}'}")
                if ok_:
                    entries += 1
                    events.append(f"🟢 <b>BUY {sym}</b> ({label} breakout)\nEntry ~{tick.ask:.6g} · Stop {sl:.6g} · "
                                  f"Lots {lot}\nRisk ${risk_usd:.2f} ({risk_usd / balance * 100:.1f}%)")
                else:
                    events.append(f"❌ {sym} order rejected ({code} {msg})")
    log(f"Checked {sum(1 for s in syms.values() if s)} symbols; {entries} new entr{'y' if entries == 1 else 'ies'}.")
    health["open"] = len(br.positions())

    # 5. Telegram replies, events, daily summary
    def status():
        lines = ["🛰️ <b>Momentum bot — status</b>"]
        a = mt5.account_info()
        if a:
            lines.append(f"Balance <b>${a.balance:.2f}</b> · Equity <b>${a.equity:.2f}</b>")
        lines.append(f"New trades: {'PAUSED ⏸️' if control.get('paused') else 'on'} · Risk {risk:g}%/trade")
        ps = br.positions()
        lines.append(f"\n<b>Open trades: {len(ps)}</b>")
        for p in ps:
            lines.append(f"• {p.symbol} ({'Daily' if engine_of(p) == 'mom_d1' else '4H'}) "
                         f"{p.volume} lot, stop {p.sl:.6g}, P&L ${p.profit:+.2f}")
        closed = [d for d in br.deals(now - pd.Timedelta(days=7)) if d.entry == mt5.DEAL_ENTRY_OUT]
        lines.append(f"\nLast 7 days: {len(closed)} closed, <b>${sum(d.profit for d in closed):+.2f}</b>")
        lines.append("\n" + filter_text(btc_d, now))
        return "\n".join(lines)

    def trades():
        ds = [d for d in br.deals(now - pd.Timedelta(days=90)) if d.entry == mt5.DEAL_ENTRY_OUT][-10:]
        if not ds:
            return "🛰️ No closed momentum trades yet."
        return "🛰️ <b>Last momentum trades</b>\n" + "\n".join(
            f"• {br.utc(d.time):%d %b} {d.symbol}: <b>${d.profit:+.2f}</b>" for d in ds)

    if live:
        for r in requests_:
            if r["cmd"] == "status":
                notify(status())
            elif r["cmd"] == "trades":
                notify(trades())
            elif r["cmd"] == "run":
                notify("✅ Check done. " + (f"{len(events)} update(s) below." if events else
                                           f"Nothing new. {'BTC filter on' if on else 'BTC filter off'}, "
                                           f"{health['open']} open trade(s). /log for details."))
            done.append(r["id"])
        if first_login and creds:
            notify(f"✅ <b>Logged in to MT5</b> ({login} on {server}).\n\n" + status())
        if events:
            notify("\n\n".join(events))
        today = now.strftime("%Y-%m-%d")
        if now.hour >= getattr(config, "MONITOR_DAILY_HOUR", 7) and state.get("summary_day") != today:
            if notify("☀️ <b>Daily summary</b>\n\n" + status()):
                state["summary_day"] = today
    return finish(0, f"ok, {entries} new trade(s)")


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log("MetaTrader5 package not installed (this runs on the Windows runner).")
        return 1
    token = os.environ.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    state = load_json(RUNNER_STATE, token)
    control = load_json(CONTROL_STATE, token)
    code = 1
    try:
        code = run(mt5, dict(os.environ), state, control)
    except Exception as e:
        log(f"Run failed: {type(e).__name__}")
        traceback.print_exc(limit=2)
        state.setdefault("health", {})["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        state["result"] = "crashed"
        state["last_run"] = datetime.now(timezone.utc).timestamp()
        if os.environ.get("MODE", "live") == "live":
            chat = os.environ.get("MOMENTUM_TELEGRAM_CHAT_ID") or control.get("tg_chat")
            tg_send(requests, token, chat, f"⚠️ Momentum bot run failed: {type(e).__name__}: {str(e)[:200]}")
    finally:
        save_json(RUNNER_STATE, state, token)
        try:
            mt5.shutdown()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
