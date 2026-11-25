"""
Momentum Breakout bot on GitHub Actions — no VPS.

Every hour a GitHub Windows machine installs MetaTrader 5, logs in, and runs this once:
  1. manage open momentum trades (raise trailing stops; close daily-engine trades when the BTC filter turns off)
  2. report trades that closed since the last run
  3. look for new breakouts and buy (same rules as momentum_strategy.py on the VPS bot)
  4. answer Telegram commands (/status /trades /filter /help) and send a daily summary
then exits. State (Telegram chat/offset, last run, filter status) is kept in the Actions cache.

The repository is public, so Actions logs are public: this script never prints the account number,
balance, prices or trades. Those go to Telegram only.

Env: MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH (terminal64.exe), MOMENTUM_TELEGRAM_TOKEN,
     MOMENTUM_TELEGRAM_CHAT_ID (optional), MOMENTUM_RISK_PCT (default 5), MODE=live|dry, CLOUD_STATE (state file)
"""

import json
import math
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config
import momentum_strategy as M

MAGIC = 26112501
COMMENT = {"mom_4h": "MOM 4H", "mom_d1": "MOM D1"}
STATE_PATH = os.environ.get("CLOUD_STATE", os.path.join("cloud_state", "state.json"))


def log(msg):
    """Public log line: never pass account data, prices or balances here."""
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(st, f)


# ── Telegram ───────────────────────────────────────────────────────────────────

class Telegram:
    def __init__(self, token, chat_id, state, http=requests):
        self.token, self.state, self.http = token or "", state, http
        self.chat = str(chat_id or state.get("tg_chat") or "") or None

    def send(self, text):
        if not self.token or not self.chat:
            return False
        try:
            r = self.http.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                               json={"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": True}, timeout=15)
            return r.status_code == 200
        except Exception:
            return False

    def commands(self):
        """New commands from the owner chat. The first /start claims the bot if no chat id is configured."""
        if not self.token:
            return []
        params = {"timeout": 0}
        if self.state.get("tg_offset"):
            params["offset"] = self.state["tg_offset"]
        try:
            data = self.http.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params=params, timeout=15).json()
        except Exception:
            return []
        out = []
        for u in data.get("result", []):
            self.state["tg_offset"] = u["update_id"] + 1
            msg = u.get("message") or u.get("edited_message") or {}
            cid = str((msg.get("chat") or {}).get("id") or "")
            text = (msg.get("text") or "").strip()
            if not cid or not text:
                continue
            if self.chat is None and text.lower().startswith("/start"):
                self.chat = cid
                self.state["tg_chat"] = cid
            if cid == self.chat:
                out.append(text.split()[0].split("@")[0].lower())
        return out


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


def run(mt5, env, state, http=requests, now=None):
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    mode = env.get("MODE", "live")
    live = mode == "live"
    tg = Telegram(env.get("MOMENTUM_TELEGRAM_TOKEN"), env.get("MOMENTUM_TELEGRAM_CHAT_ID"), state, http)
    notify = tg.send if live else (lambda _t: False)
    events = []

    if not env.get("MT5_LOGIN") or not env.get("MT5_PASSWORD"):
        log("MT5_LOGIN / MT5_PASSWORD secrets are not set — nothing to do.")
        if live and not state.get("asked_secrets"):
            tg.commands()
            if notify("🛰️ Add GitHub secrets <b>MT5_LOGIN</b> and <b>MT5_PASSWORD</b> so I can trade."):
                state["asked_secrets"] = True
        return 0

    br = Broker(mt5)
    ok, err = br.connect(env.get("MT5_PATH"), env["MT5_LOGIN"], env["MT5_PASSWORD"],
                         env.get("MT5_SERVER") or "Exness-MT5Trial9", portable=env.get("MT5_PORTABLE") == "1")
    if not ok:
        log("MT5 login failed.")
        last = state.get("login_alert", 0)
        if live and now.timestamp() - last > 6 * 3600:
            tg.commands()
            notify(f"⚠️ <b>MT5 login failed</b> ({err}). Check the MT5_LOGIN / MT5_PASSWORD / MT5_SERVER secrets.")
            state["login_alert"] = now.timestamp()
        return 1
    state.pop("login_alert", None)
    log("MT5 connected.")

    term = mt5.terminal_info()
    if live and term is not None and not term.trade_allowed and not state.get("algo_alert"):
        notify("⚠️ MT5 reports Algo Trading is disabled; orders may be rejected.")
        state["algo_alert"] = True

    syms = {s: br.resolve(s) for s in getattr(config, "MOMENTUM_SYMBOLS", [])}
    btc = br.resolve(getattr(config, "MOMENTUM_REGIME_SYMBOL", "BTCUSDm"))
    if not btc:
        log("BTC symbol not available — stopping.")
        return 1
    br.calibrate(btc, now)
    btc_d = br.bars(btc, "mom_d1")
    on = M.regime_ok(btc_d, now)
    log(f"Market filter {'ON' if on else 'OFF'}.")
    if state.get("regime") is not None and state["regime"] != on:
        events.append(f"🛰️ <b>Market filter {'ON' if on else 'OFF'}</b> — BTC closed "
                      f"{'above' if on else 'below'} its {M.REGIME_EMA}-day EMA.")
    state["regime"] = on

    acct = mt5.account_info()
    balance = float(acct.balance) if acct else 0.0

    # 1. manage open trades
    for pos in br.positions():
        eng = engine_of(pos)
        df = br.bars(pos.symbol, eng)
        action, new_sl = M.manage(df, eng, br.utc(pos.time), pos.sl, on, now)
        if action == "exit":
            if live:
                ok, code, why = br.close(pos)
                events.append(f"🛰️ {'Closed' if ok else '⚠️ Could not close'} {pos.symbol} — market filter off"
                              + ("" if ok else f" ({code} {why})"))
        elif action == "trail":
            tick = mt5.symbol_info_tick(pos.symbol)
            info = mt5.symbol_info(pos.symbol)
            min_gap = (getattr(info, "trade_stops_level", 0) or 0) * info.point
            if new_sl > (pos.sl or 0) + info.point and new_sl < tick.bid - min_gap and live:
                ok, code, why = br.modify_sl(pos, new_sl)
                if ok:
                    events.append(f"🛰️ {pos.symbol}: stop raised to {new_sl:.6g}")

    # 2. trades closed since the last run
    since = pd.Timestamp(state["last_run"], unit="s", tz="UTC") if state.get("last_run") else now - pd.Timedelta(hours=2)
    for d in br.deals(since):
        if d.entry == mt5.DEAL_ENTRY_OUT:
            res = d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
            events.append(f"🏁 <b>Closed {d.symbol}</b> {'✅' if res > 0 else '❌'} <b>${res:+.2f}</b>")

    # 3. new entries
    entries = 0
    if on:
        cap = getattr(config, "MOMENTUM_MAX_RISK_PCT", 8.0)
        risk = float(env.get("MOMENTUM_RISK_PCT") or getattr(config, "MOMENTUM_RISK_PCT", 5.0))
        max_open = getattr(config, "MOMENTUM_MAX_OPEN", 4)
        for eng in getattr(config, "MOMENTUM_ENGINES", ["mom_4h", "mom_d1"]):
            for name, sym in syms.items():
                open_pos = br.positions()
                if not sym or any(p.symbol == sym for p in open_pos):
                    continue
                if len(open_pos) >= max_open:
                    break
                df = br.bars(sym, eng)
                setup = M.entry_signal(sym, df, eng, on, now)
                if not setup:
                    continue
                hrs = M.ENGINES[eng]["tf_hours"]
                candle_close = pd.Timestamp(setup["signal_time"]) + pd.Timedelta(hours=hrs)
                if any(d.symbol == sym and d.entry == mt5.DEAL_ENTRY_IN for d in br.deals(candle_close)):
                    continue      # already bought this breakout in an earlier run
                info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
                if info.spread * info.point > getattr(config, "MOMENTUM_MAX_SPREAD_FRAC", 0.15) * setup["sl_dist"]:
                    events.append(f"🛰️ {sym} breakout skipped: spread too wide for the stop")
                    continue
                lot, risk_usd, why = size_trade(info, setup["sl_dist"], balance, risk, cap)
                if lot is None:
                    events.append(f"🛰️ {sym} breakout skipped: {why}")
                    continue
                sl = tick.ask - setup["sl_dist"]
                if not live:
                    entries += 1
                    continue
                ok, code, why = br.buy(sym, lot, sl, COMMENT[eng])
                if ok:
                    entries += 1
                    events.append(f"🟢 <b>BUY {sym}</b> ({M.ENGINES[eng]['label']} breakout)\n"
                                  f"Entry ~{tick.ask:.6g} · Stop {sl:.6g} · Lots {lot}\n"
                                  f"Risk ${risk_usd:.2f} ({risk_usd / balance * 100:.1f}%)")
                else:
                    events.append(f"❌ {sym} order rejected ({code} {why})")
    log(f"Checked {sum(1 for s in syms.values() if s)} symbols; {entries} new entr{'y' if entries == 1 else 'ies'}.")

    # 4. Telegram: commands, events, daily summary
    def status():
        lines = ["🛰️ <b>Momentum Breakout — status</b>"]
        a = mt5.account_info()
        if a:
            lines.append(f"Balance <b>${a.balance:.2f}</b> · Equity <b>${a.equity:.2f}</b>")
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

    handlers = {"/status": status, "/trades": trades, "/filter": lambda: filter_text(btc_d, now)}
    help_txt = ("🛰️ <b>Momentum Breakout (GitHub cloud)</b>\nI run once an hour, so replies can take up to an hour.\n"
                "/status · /trades · /filter · /help")
    if live:
        first = tg.chat is None
        for cmd in tg.commands():
            if cmd in handlers:
                notify(handlers[cmd]())
            elif cmd in ("/start", "/help"):
                notify(help_txt if not (first and cmd == "/start") else "✅ This chat now receives the momentum bot.\n\n" + help_txt)
        if events:
            notify("\n\n".join(events))
        today = now.strftime("%Y-%m-%d")
        if now.hour >= getattr(config, "MONITOR_DAILY_HOUR", 7) and state.get("summary_day") != today:
            if notify("☀️ <b>Daily summary</b>\n\n" + status()):
                state["summary_day"] = today
        if not state.get("hello") and notify(f"🛰️ <b>Momentum bot is live on GitHub</b> (checks hourly).\n\n{filter_text(btc_d, now)}"):
            state["hello"] = True
    state["last_run"] = now.timestamp()
    return 0


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log("MetaTrader5 package not installed (this runs on the Windows runner).")
        return 1
    state = load_state()
    code = 1
    try:
        code = run(mt5, dict(os.environ), state)
    except Exception as e:
        log(f"Run failed: {type(e).__name__}")
        traceback.print_exc(limit=3)
        if os.environ.get("MODE", "live") == "live":
            Telegram(os.environ.get("MOMENTUM_TELEGRAM_TOKEN"), os.environ.get("MOMENTUM_TELEGRAM_CHAT_ID"),
                     state).send(f"⚠️ Momentum bot run failed: {type(e).__name__}: {str(e)[:200]}")
    finally:
        save_state(state)
        try:
            mt5.shutdown()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
