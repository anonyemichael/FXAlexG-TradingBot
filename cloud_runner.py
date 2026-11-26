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

import copy
import math
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config
import metals_strategy as MS
import momentum_strategy as M
from cloud_common import (CONTROL_STATE, DEFAULT_SERVER, RUNNER_GOLD_STATE, RUNNER_STATE, gold_text, load_json,
                          save_json, tg_send)

MAGIC = 26112501
MAGIC_TEST = MAGIC + 1        # /testtrade positions (opened and closed in the same run)
MAGIC_METAL = MAGIC + 2       # gold 1H breakout trades (buys and sells)
COMMENT = {"mom_1h": "MOM 1H", "mom_4h": "MOM 4H", "mom_d1": "MOM D1"}


MONEY_SCALE = 1.0             # 0.01 on cent accounts (balance and profits in US cents)


def usd(v, sign=False):
    v = float(v) * MONEY_SCALE
    return ("$" + format(v, "+.2f")) if sign else ("$" + format(v, ".2f"))


def log(msg):
    """Public log line: never pass account data, prices or balances here."""
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ── MetaTrader 5 ───────────────────────────────────────────────────────────────

class Broker:
    def __init__(self, mt5):
        self.mt5 = mt5
        self.offset = 0          # trade-server time minus UTC, in hours
        self.names = {}
        self.data = {}           # (symbol, tf hours) -> (candles received, wanted, newest candle time)
        self._bars = {}

    def connect(self, path, login, password, server, portable=False):
        kw = {"login": int(login), "password": password, "server": server, "timeout": 180000}
        if path:
            kw["path"] = path
        if portable:
            kw["portable"] = True      # settings (incl. Algo Trading on) live next to terminal64.exe
            if path:
                self._launch_with_algo_on(path)
        if not self.mt5.initialize(**kw):
            return False, str(self.mt5.last_error())
        return True, ""

    @staticmethod
    def _launch_with_algo_on(path):
        """Start terminal64.exe ourselves with a startup config whose [Experts] section switches the
        "Algo Trading" button on (Enabled=1) and keeps it on when the account changes (Account=0);
        initialize() then attaches to this running terminal."""
        import subprocess
        import time
        ini = os.path.join(os.path.dirname(path), "algo_on.ini")
        body = ("[Experts]\r\nAllowLiveTrading=1\r\nAllowDllImport=0\r\nEnabled=1\r\n"
                "Account=0\r\nProfile=0\r\n")
        try:
            with open(ini, "wb") as f:
                f.write(b"\xff\xfe" + body.encode("utf-16-le"))
            subprocess.Popen([path, "/portable", f"/config:{ini}"])
            time.sleep(10)
        except OSError:
            pass

    def calibrate(self, symbol, now):
        """Trade-server clock minus UTC, whole hours (Exness is UTC+0, but don't assume it)."""
        tick = self.mt5.symbol_info_tick(symbol)
        if tick and tick.time:
            off = round((tick.time - now.timestamp()) / 3600)
            self.offset = off if abs(off) <= 14 else 0      # a stale tick (weekend/holiday) can't be an offset

    def resolve(self, name):
        """Exness Standard accounts use an 'm' suffix (SOLUSDm), Standard Cent a 'c' suffix (XAUUSDc)."""
        if name in self.names:
            return self.names[name]
        base = name[:-1] if name[-1:] in ("m", "c") else name
        for cand in dict.fromkeys((name, base, base + "m", base + "c")):
            if self.mt5.symbol_info(cand) is not None:
                self.mt5.symbol_select(cand, True)
                self.names[name] = cand
                return cand
        self.names[name] = None
        return None

    retry_wait = 2.0          # seconds between attempts while a fresh terminal downloads history
    TF_HOURS = {"mom_1h": 1, "met_1h": 1, "mom_4h": 4}

    def bars(self, symbol, engine, n=300):
        """Candles for one symbol/timeframe. A freshly installed terminal downloads history on demand, so a
        short answer is retried a few times. What came back is recorded in self.data for the data check."""
        tf = {"mom_4h": self.mt5.TIMEFRAME_H4, "met_1h": self.mt5.TIMEFRAME_H1,
              "mom_1h": self.mt5.TIMEFRAME_H1}.get(engine, self.mt5.TIMEFRAME_D1)
        key = (symbol, self.TF_HOURS.get(engine, 24), n)
        if key in self._bars:
            return self._bars[key]
        rates = None
        for attempt in range(3):
            rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, n)
            if rates is not None and len(rates) >= n * 0.95:
                break
            if attempt < 2 and self.retry_wait:
                import time
                time.sleep(self.retry_wait)
        df = None
        if rates is not None and len(rates):
            df = pd.DataFrame(rates)
            df.index = pd.to_datetime(df["time"] - self.offset * 3600, unit="s", utc=True)
            df = df[["open", "high", "low", "close"]].astype(float)
        self.data[(symbol, key[1])] = (0 if df is None else len(df), n, None if df is None else df.index[-1])
        self._bars[key] = df
        return df

    def data_check(self, now):
        """(ok, total, problems): every candle set long enough and its newest candle no older than the
        market's own last price allows (a closed market, e.g. gold at the weekend, is not a problem)."""
        problems = []
        for (sym, tfh), (got, want, last) in sorted(self.data.items()):
            name = f"{sym} {'D1' if tfh == 24 else f'H{tfh}'}"
            if got == 0:
                problems.append(f"{name}: no candles")
                continue
            if got < want * 0.95:
                problems.append(f"{name}: only {got} of {want} candles")
            tick = self.mt5.symbol_info_tick(sym)
            ref = self.utc(tick.time) if tick is not None and tick.time else now
            if ref - last > pd.Timedelta(hours=2 * tfh + 1):
                problems.append(f"{name}: newest candle {last:%d %b %H:%M} is behind the live price")
        return len(self.data) - len({p.split(':')[0] for p in problems}), len(self.data), problems

    def utc(self, server_ts):
        return pd.Timestamp(server_ts - self.offset * 3600, unit="s", tz="UTC")

    def positions(self, magic=MAGIC):
        return [p for p in (self.mt5.positions_get() or []) if p.magic == magic]

    def all_positions(self):
        return self.positions(MAGIC) + self.positions(MAGIC_METAL)

    def deals(self, since_utc, magics=(MAGIC, MAGIC_METAL)):
        frm = datetime.fromtimestamp(since_utc.timestamp() + self.offset * 3600, tz=timezone.utc)
        to = datetime.now(timezone.utc) + timedelta(days=3650)      # "until now", whatever the clocks say
        return [d for d in (self.mt5.history_deals_get(frm, to) or []) if d.magic in magics]

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

    def order(self, symbol, direction, lot, sl, comment, magic=MAGIC):
        """direction +1 = buy at the ask, -1 = sell at the bid."""
        info, tick = self.mt5.symbol_info(symbol), self.mt5.symbol_info_tick(symbol)
        return self._send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
                           "type": self.mt5.ORDER_TYPE_BUY if direction > 0 else self.mt5.ORDER_TYPE_SELL,
                           "price": tick.ask if direction > 0 else tick.bid, "sl": round(sl, info.digits),
                           "tp": 0.0, "deviation": 50, "magic": magic, "comment": comment,
                           "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)})

    def buy(self, symbol, lot, sl, comment, magic=MAGIC):
        return self.order(symbol, 1, lot, sl, comment, magic)

    def modify_sl(self, pos, sl):
        info = self.mt5.symbol_info(pos.symbol)
        return self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": pos.ticket,
                           "sl": round(sl, info.digits), "tp": pos.tp, "magic": getattr(pos, "magic", MAGIC)})

    def close(self, pos):
        """Close at market: a buy is closed by selling at the bid, a sell by buying at the ask."""
        info, tick = self.mt5.symbol_info(pos.symbol), self.mt5.symbol_info_tick(pos.symbol)
        short = side_of(pos) < 0
        return self._send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
                           "type": self.mt5.ORDER_TYPE_BUY if short else self.mt5.ORDER_TYPE_SELL,
                           "position": pos.ticket, "price": tick.ask if short else tick.bid,
                           "deviation": 50, "magic": getattr(pos, "magic", MAGIC), "comment": "MOM exit",
                           "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(info)})


def side_of(pos):
    """+1 for a buy position, -1 for a sell (MT5 POSITION_TYPE_SELL = 1)."""
    return -1 if getattr(pos, "type", 0) == 1 else 1


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
            return None, min_risk, (f"minimum lot {info.volume_min} would risk {usd(min_risk)} "
                                    f"({min_risk / balance * 100:.0f}% of balance), cap is {cap_pct:.0f}%")
    lot = round(min(lot, info.volume_max), dec)
    return lot, lot * loss_per_lot, None


# ── one run ────────────────────────────────────────────────────────────────────

def engine_of(pos):
    c = (pos.comment or "").upper()
    return "mom_d1" if "D1" in c else "mom_1h" if "1H" in c else "mom_4h"


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


BUY_MODES = (1, 4)            # SYMBOL_TRADE_MODE_LONGONLY, SYMBOL_TRADE_MODE_FULL
MODE_NAMES = {0: "disabled", 1: "long only", 2: "short only", 3: "close only", 4: "full"}


def can_buy(info):
    return getattr(info, "trade_mode", 4) in BUY_MODES


def can_trade(info, direction):
    return getattr(info, "trade_mode", 4) in (BUY_MODES if direction > 0 else (2, 4))


def permissions(mt5):
    """The three switches that must all be on for orders to go through (None = unknown)."""
    term, acct = mt5.terminal_info(), mt5.account_info()
    return {"algo": None if term is None else bool(getattr(term, "trade_allowed", True)),
            "account": None if acct is None else bool(getattr(acct, "trade_allowed", True)),
            "ea": None if acct is None else bool(getattr(acct, "trade_expert", True))}


def _yn(v, yes="yes ✅", no="NO ❌"):
    return "unknown" if v is None else (yes if v else no)


def test_trade(mt5, br, syms, note):
    """Open the smallest possible position and close it straight away, to prove orders really execute.
    Tries up to 3 coins (cheapest first) and then EURUSD; if all are refused, explains why.
    Returns the Telegram text."""
    perm = permissions(mt5)
    cands, blocked = [], []
    for sym in [s for s in syms.values() if s]:
        info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if not info or not tick:
            continue
        if not can_buy(info):
            blocked.append(f"{sym} ({MODE_NAMES.get(getattr(info, 'trade_mode', None), 'unknown')})")
            continue
        cands.append((info.volume_min * getattr(info, "trade_contract_size", 1.0) * tick.ask, sym))
    order = [s for _, s in sorted(cands)][:3]
    fx = br.resolve("EURUSDm")
    if fx and fx not in order:
        order.append(fx)
    fails = []
    for sym in order:
        info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        if not info or not tick:
            continue
        ok, code, msg = br.buy(sym, info.volume_min, tick.ask * 0.95, "TEST", magic=MAGIC_TEST)
        note(f"Test trade BUY {sym} {info.volume_min}: {'ok' if ok else f'FAILED {code} {msg}'}")
        log(f"Test order: {'opened' if ok else f'rejected (code {code})'}.")       # public log: no symbol/size/price
        if not ok:
            fails.append((sym, code, msg))
            continue
        pos = next((p for p in (mt5.positions_get() or []) if p.magic == MAGIC_TEST and p.symbol == sym), None)
        if pos is None:
            return f"⚠️ Test BUY {sym} was accepted but no open position was found. Check MT5."
        ok2, code2, msg2 = br.close(pos)
        note(f"Test trade CLOSE {sym}: {'ok' if ok2 else f'FAILED {code2} {msg2}'}")
        log(f"Test order: {'closed' if ok2 else f'close rejected (code {code2})'}.")
        txt = (f"✅ <b>MT5 test trade worked</b>\nBought {info.volume_min} lot {sym} at ~{tick.ask:.6g} "
               f"(ticket #{pos.ticket}) and {'closed it again' if ok2 else f'could NOT close it ({code2} {msg2}) — close it in MT5'}.\n"
               "Cost: about one spread. The bot can place and close real orders on your account.")
        if fails:
            txt += "\n\nRefused first: " + "; ".join(f"{s} ({c} {m})" for s, c, m in fails)
        if blocked:
            txt += "\nNot tradable on this account (skipped by the bot): " + ", ".join(blocked)
        return txt

    lines = ["❌ <b>Test trade failed</b> — the broker refused every order."]
    lines += [f"• {s}: {c} {m}" for s, c, m in fails]
    if blocked:
        lines.append("Not tradable on this account: " + ", ".join(blocked))
    lines += ["", "<b>Checks</b>",
              f"Algo Trading in MT5: {_yn(perm['algo'], 'ON ✅', 'OFF ❌')}",
              f"This login may trade: {_yn(perm['account'])}",
              f"Broker allows API/EA trading: {_yn(perm['ea'])}", ""]
    codes = {c for _, c, _ in fails}
    if perm["account"] is False:
        lines.append("👉 This login is <b>read-only</b>. That is what happens with the <b>investor</b> password. "
                     "Send <code>/login ACCOUNT TRADING_PASSWORD SERVER</code> with the <b>trading</b> password "
                     "(Exness Personal Area → your account → ⚙️ → Change trading password if you don't know it).")
    elif perm["algo"] is False or 10027 in codes:
        lines.append("👉 Algo Trading is off in the terminal. The bot switches it on at every start; "
                     "if this repeats, tell Claude.")
    elif perm["ea"] is False or 10026 in codes:
        lines.append("👉 The broker has disabled automated trading on this account. Open a new MT5 account "
                     "in the Exness Personal Area and send /login with it.")
    elif 10018 in codes:
        lines.append("👉 Market closed for these symbols right now — try /testtrade again later.")
    elif 10019 in codes:
        lines.append("👉 Not enough free margin for even the smallest order.")
    elif 10017 in codes:
        lines.append("👉 The server says trading is disabled for this account (e.g. an archived/expired demo "
                     "or an account type without these symbols). Open a new MT5 account (Standard) in the "
                     "Exness Personal Area and send /login with it.")
    elif not order:
        lines.append("👉 None of the coins can be bought on this account.")
    return "\n".join(lines)


def run(mt5, env, state, control=None, http=requests, now=None):
    """state: this side's encrypted state (written back by main). control: the Telegram listener's state
    (read-only here). Returns the process exit code."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    control = control or {}
    live = env.get("MODE", "live") == "live"
    if not live:                # push "dry" checks work on a copy: they must not move the hourly clock,
        state["last_dry_run"] = now.timestamp()            # hide closed-trade reports or change alerts
        state = copy.deepcopy(state)
    token = env.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    chat = str(env.get("MOMENTUM_TELEGRAM_CHAT_ID") or control.get("tg_chat")
               or getattr(config, "MOMENTUM_TELEGRAM_CHAT_ID", "") or "") or None
    engines = set((env.get("BOT_ENGINES") or "crypto,gold").split(","))
    crypto_on = "crypto" in engines
    gold_pass = env.get("BOT_PASS") == "gold"              # the separate (cent) account that trades gold only
    prefix = "🥇 <b>Gold account</b> · " if gold_pass else ""
    notify = (lambda t: tg_send(http, token, chat, prefix + t)) if live else (lambda _t: False)
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

    perm = permissions(mt5)
    log(f"Algo Trading {_yn(perm['algo'], 'ON', 'OFF')} · login may trade: {_yn(perm['account'], 'yes', 'NO')}"
        f" · broker allows API trading: {_yn(perm['ea'], 'yes', 'NO')}.")        # no account data
    health["algo"] = "on" if perm["algo"] is not False else "OFF — orders will be rejected"
    health["trading"] = ("allowed" if perm["account"] is not False else
                         "NOT allowed — read-only login (investor password?)")
    if perm["ea"] is False:
        health["trading"] += " · broker disabled API/EA trading"
    if live and perm["algo"] is False and not state.get("algo_alert"):
        notify("⚠️ MT5 reports Algo Trading is OFF on the cloud terminal; orders will be rejected. "
               "The bot switches it on at every start — if this repeats, tell Claude.")
        state["algo_alert"] = True
    elif perm["algo"]:
        state.pop("algo_alert", None)
    if live and perm["account"] is False and not state.get("readonly_alert"):
        notify("⚠️ <b>This MT5 login is read-only</b> — every order will be refused (10017). That is what "
               "happens with the <b>investor</b> password. Send <code>/login ACCOUNT TRADING_PASSWORD SERVER</code> "
               "with the trading password.")
        state["readonly_alert"] = True
    elif perm["account"]:
        state.pop("readonly_alert", None)

    syms = {s: br.resolve(s) for s in getattr(config, "MOMENTUM_SYMBOLS", [])} if crypto_on else {}
    missing = [s for s, v in syms.items() if not v]
    if missing:
        note(f"Not on this account: {', '.join(missing)}")
    btc = br.resolve(getattr(config, "MOMENTUM_REGIME_SYMBOL", "BTCUSDm")) if crypto_on else None
    on, btc_d = False, None
    if crypto_on and not btc:
        log("BTC symbol not available — crypto skipped.")
        note("BTC not on this account (e.g. a cent account) — crypto skipped, gold still checked")
        health["error"] = "BTC symbol missing: crypto skipped"
        crypto_on = False
        syms = {}
    if crypto_on:
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
    global MONEY_SCALE
    MONEY_SCALE = 0.01 if str(getattr(acct, "currency", "USD") or "").upper() == "USC" else 1.0
    balance = float(acct.balance) if acct else 0.0
    health["balance"] = f"{usd(balance)}"

    def close_pos(pos, why):
        ok_, code, msg = br.close(pos) if live else (True, None, "")
        note(f"Close {pos.symbol} ({why}): {'done' if ok_ else f'FAILED {code} {msg}'}")
        return ok_, code, msg

    # 1. queued Telegram requests that change positions
    for r in requests_:
        if r["cmd"] == "close":
            want = r["arg"].upper().rstrip("MC")
            hits = [p for p in br.all_positions() if p.symbol.upper().rstrip("MC") == want]
            if not hits and not gold_pass:
                notify(f"🛰️ No open bot trade on {r['arg']}.")
            for p in hits:
                ok_, code, msg = close_pos(p, "you asked")
                notify(f"{'✅ Closed' if ok_ else '❌ Could not close'} {p.symbol}" + ("" if ok_ else f" ({code} {msg})"))
        elif r["cmd"] == "closeall":
            ps = br.all_positions()
            res = [close_pos(p, "close all")[0] for p in ps]
            if ps or not gold_pass:
                notify(f"✅ Closed {sum(res)} of {len(ps)} bot trades." if ps else "🛰️ No open bot trades.")
        elif r["cmd"] == "testtrade" and live:
            pool = {**syms, **{x: br.resolve(x) for x in MS.SYMBOLS}} if gold_pass or not syms else syms
            notify(test_trade(mt5, br, pool, note))

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
            if tick is None or info is None:
                note(f"{pos.symbol}: no price right now, stop left at {pos.sl:.6g}")
                continue
            min_gap = (getattr(info, "trade_stops_level", 0) or 0) * info.point
            if new_sl > (pos.sl or 0) + info.point and new_sl < tick.bid - min_gap:
                if live:
                    ok_, code, msg = br.modify_sl(pos, new_sl)
                    note(f"{pos.symbol}: stop {pos.sl:.6g} -> {new_sl:.6g} {'ok' if ok_ else f'FAILED {code} {msg}'}")
                    if ok_:
                        events.append(f"🛰️ {pos.symbol}: stop raised to {new_sl:.6g}")
        else:
            note(f"{pos.symbol}: holding, stop {pos.sl:.6g}, P&L {usd(pos.profit, True)}")

    # 2b. gold trades (buys and sells): trail the stop after every 1H close
    gold_on = getattr(config, "METALS_ENABLED", True) and "gold" in engines
    gsyms = {s: br.resolve(s) for s in (MS.SYMBOLS if gold_on else [])}
    if not crypto_on and any(gsyms.values()):
        br.calibrate(next(v for v in gsyms.values() if v), now)
    for pos in br.positions(MAGIC_METAL):
        d = side_of(pos)
        new_sl = MS.manage(br.bars(pos.symbol, "met_1h", 400), d, br.utc(pos.time), pos.sl,
                           getattr(pos, "price_open", None), now)
        tick, info = mt5.symbol_info_tick(pos.symbol), mt5.symbol_info(pos.symbol)
        if tick is None or info is None:
            note(f"{pos.symbol}: no price right now, stop left at {pos.sl:.6g}")
            continue
        min_gap = (getattr(info, "trade_stops_level", 0) or 0) * info.point
        valid = new_sl is not None and (new_sl < tick.bid - min_gap if d > 0 else new_sl > tick.ask + min_gap) \
            and abs(new_sl - (pos.sl or 0)) > info.point
        if valid and live:
            ok_, code, msg = br.modify_sl(pos, new_sl)
            note(f"{pos.symbol} {'BUY' if d > 0 else 'SELL'}: stop {pos.sl:.6g} -> {new_sl:.6g} "
                 f"{'ok' if ok_ else f'FAILED {code} {msg}'}")
            if ok_:
                events.append(f"🥇 {pos.symbol} {'BUY' if d > 0 else 'SELL'}: stop "
                              f"{'raised' if d > 0 else 'lowered'} to {new_sl:.6g}")
        else:
            note(f"{pos.symbol} {'BUY' if d > 0 else 'SELL'}: holding, stop {pos.sl:.6g}, P&L {usd(pos.profit, True)}")

    # 3. trades closed since the last run
    since = pd.Timestamp(state["last_run"], unit="s", tz="UTC") if state.get("last_run") else now - pd.Timedelta(hours=2)
    for d in br.deals(since):
        if d.entry == mt5.DEAL_ENTRY_OUT:
            res = d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
            events.append(f"🏁 <b>Closed {d.symbol}</b> {'✅' if res > 0 else '❌'} <b>{usd(res, True)}</b>")
            note(f"Closed {d.symbol}: {usd(res, True)}")

    # 4. new entries
    entries = 0
    risk = float(control.get("risk_pct") or env.get("MOMENTUM_RISK_PCT") or getattr(config, "MOMENTUM_RISK_PCT", 5.0))
    if control.get("paused"):
        note("Paused by you (/resume to allow new trades)")
    elif not crypto_on:
        pass
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
                if not can_buy(info):
                    note(f"{sym} {label}: breakout, but buying {sym} is "
                         f"{MODE_NAMES.get(getattr(info, 'trade_mode', None), 'not allowed')} on this account")
                    continue
                if info.spread * info.point > getattr(config, "MOMENTUM_MAX_SPREAD_FRAC", 0.15) * setup["sl_dist"]:
                    note(f"{sym} {label}: breakout skipped, spread too wide")
                    events.append(f"🛰️ {sym} breakout skipped: spread too wide for the stop")
                    continue
                eng_risk = min(risk, getattr(config, "MOMENTUM_1H_RISK_PCT", 2.0)) if eng == "mom_1h" else risk
                lot, risk_usd, why = size_trade(info, setup["sl_dist"], balance, eng_risk, cap)
                if lot is None:
                    note(f"{sym} {label}: breakout skipped, {why}")
                    skey = f"{sym}|{eng}|{setup['signal_time']}"
                    if skey not in state.get("skipped", []):
                        state["skipped"] = (state.get("skipped", []) + [skey])[-40:]
                        events.append(f"🛰️ {sym} {label} breakout skipped: {why}")
                    continue
                sl = tick.ask - setup["sl_dist"]
                if not live:
                    entries += 1
                    continue
                ok_, code, msg = br.buy(sym, lot, sl, COMMENT[eng])
                note(f"BUY {sym} {lot} lot, stop {sl:.6g}, risk {usd(risk_usd)}: {'ok' if ok_ else f'REJECTED {code} {msg}'}")
                if ok_:
                    entries += 1
                    events.append(f"🟢 <b>BUY {sym}</b> ({label} breakout)\nEntry ~{tick.ask:.6g} · Stop {sl:.6g} "
                                  f"({setup['sl_dist'] / tick.ask * 100:.1f}% away, trails up) · Lots {lot}\n"
                                  f"Risk {usd(risk_usd)} ({risk_usd / balance * 100:.1f}% of {usd(balance)})\n"
                                  f"Why: {setup['met'][1]}; {setup['met'][0]}")
                else:
                    events.append(f"❌ {sym} order rejected ({code} {msg})")

    # 4b. gold 1H breakout — buys above its 200-day EMA, sells below (independent of the BTC filter)
    gold_note = "off"
    grisk = float(control.get("gold_risk") or env.get("METALS_RISK_PCT") or getattr(config, "METALS_RISK_PCT", 2.0))
    gcap = max(getattr(config, "METALS_MAX_RISK_PCT", 5.0), grisk)    # /goldrisk above 5 also raises the cap
    for name, sym in gsyms.items():
        if not sym:
            note(f"{name}: not on this account")
            gold_note = "not on this account"
            continue
        h1, d1 = br.bars(sym, "met_1h", 400), br.bars(sym, "mom_d1", 300)
        info, tick = mt5.symbol_info(sym), mt5.symbol_info_tick(sym)
        bars = MS.closed_bars(h1, 1, now)
        tr, dclose, ema = MS.trend(MS.closed_bars(d1, 24, now))
        g = {"symbol": sym, "trend": tr, "close": dclose, "ema": ema}
        if bars is not None and len(bars) > MS.BREAK + MS.ATR_LEN:
            a = float(MS.atr(bars).iloc[-1])
            g.update(hi=float(bars["high"].iloc[-MS.BREAK:].max()), lo=float(bars["low"].iloc[-MS.BREAK:].min()),
                     stop=MS.STOP_ATR * a)
            if info is not None and info.trade_tick_size:
                g["min_risk"] = info.volume_min * MS.STOP_ATR * a / info.trade_tick_size * info.trade_tick_value
                g["min_risk_pct"] = g["min_risk"] / balance * 100 if balance else None
                g["min_risk"] *= MONEY_SCALE
        health["gold"] = g
        if control.get("paused"):
            gold_note = "paused"
            continue
        if any(p.symbol == sym for p in br.positions(MAGIC_METAL)):
            gold_note = "trade open"
            continue
        setup = MS.entry_signal(sym, h1, d1, now)
        if not setup:
            gold_note = "no breakout"
            if tr and g.get("hi"):
                note(f"{sym} {MS.LABEL}: no breakout ({'buy above ' + format(g['hi'], ',.2f') if tr > 0 else 'sell below ' + format(g['lo'], ',.2f')})")
            else:
                note(f"{sym} {MS.LABEL}: no signal")
            continue
        d = setup["dir"]
        side = "BUY" if d > 0 else "SELL"
        sig_close = pd.Timestamp(setup["signal_time"]) + pd.Timedelta(hours=1)
        if sig_close.tzinfo is None:
            sig_close = sig_close.tz_localize("UTC")
        if any(x.symbol == sym and x.entry == mt5.DEAL_ENTRY_IN for x in br.deals(sig_close, (MAGIC_METAL,))):
            note(f"{sym} {MS.LABEL}: breakout already traded")
            gold_note = "breakout already traded"
            continue
        if not can_trade(info, d):
            note(f"{sym} {MS.LABEL}: {side} signal, but {side.lower()}ing is not allowed on this account")
            continue
        if info.spread * info.point > getattr(config, "METALS_MAX_SPREAD_FRAC", 0.15) * setup["sl_dist"]:
            note(f"{sym} {MS.LABEL}: {side} skipped, spread too wide")
            continue
        lot, risk_usd, why = size_trade(info, setup["sl_dist"], balance, grisk, gcap)
        if lot is None:
            note(f"{sym} {MS.LABEL}: {side} skipped, {why}")
            gold_note = "signal skipped (account too small)"
            if state.get("gold_skip") != setup["signal_time"]:
                state["gold_skip"] = setup["signal_time"]
                events.append(f"🥇 Gold {side} signal skipped: {why}.\nA Standard Cent account (or a bigger "
                              f"balance) lets the bot size gold trades properly. /gold for details.")
            continue
        price = tick.ask if d > 0 else tick.bid
        sl = price - d * setup["sl_dist"]
        if not live:
            entries += 1
            continue
        ok_, code, msg = br.order(sym, d, lot, sl, MS.COMMENT, MAGIC_METAL)
        note(f"{side} {sym} {lot} lot, stop {sl:.6g}, risk {usd(risk_usd)}: {'ok' if ok_ else f'REJECTED {code} {msg}'}")
        if ok_:
            entries += 1
            gold_note = f"{side.lower()} opened"
            events.append(f"{'🟢' if d > 0 else '🔴'} <b>{side} {sym}</b> ({MS.LABEL} {'breakout' if d > 0 else 'breakdown'})\n"
                          f"Entry ~{price:,.2f} · Stop {sl:,.2f} ({setup['sl_dist']:,.2f} away, trails "
                          f"{'up' if d > 0 else 'down'}) · Lots {lot}\n"
                          f"Risk {usd(risk_usd)} ({risk_usd / balance * 100:.1f}% of {usd(balance)})\n"
                          f"Why: {setup['met'][1]}; {setup['met'][0]}")
        else:
            events.append(f"❌ {sym} {side} order rejected ({code} {msg})")
    state["gold_note"] = gold_note
    ok_sets, n_sets, problems = br.data_check(now)
    health["data"] = {"ok": ok_sets, "total": n_sets, "problems": problems[:12], "at": now.timestamp()}
    log(f"Candle data: {ok_sets}/{n_sets} sets complete and current.")
    for p_ in problems:
        note(f"Data problem: {p_}")
    if problems and live and state.get("data_alert_day") != now.strftime("%Y-%m-%d"):
        state["data_alert_day"] = now.strftime("%Y-%m-%d")
        events.append("⚠️ <b>Candle data incomplete</b> — the bot skipped what it couldn't read:\n• "
                      + "\n• ".join(problems[:6]))
    log(f"Checked {sum(1 for s in syms.values() if s) + sum(1 for s in gsyms.values() if s)} symbols; "
        f"{entries} new entr{'y' if entries == 1 else 'ies'}.")
    health["open"] = len(br.all_positions())

    # 5. Telegram replies, events, daily summary
    def status():
        lines = ["🛰️ <b>" + ("status" if gold_pass else "Momentum bot — status") + "</b>"]
        a = mt5.account_info()
        if a:
            lines.append(f"Balance <b>{usd(a.balance)}</b> · Equity <b>{usd(a.equity)}</b>")
        lines.append(f"New trades: {'PAUSED ⏸️' if control.get('paused') else 'on'} · Risk "
                     + (f"{grisk:g}%/trade gold" if gold_pass else
                        f"{risk:g}%/trade crypto" + ("" if control.get("_gold_account") else f", {grisk:g}% gold")))
        ps = br.all_positions()
        lines.append(f"\n<b>Open trades: {len(ps)}</b>")
        for p in ps:
            kind = MS.LABEL if p.magic == MAGIC_METAL else M.ENGINES[engine_of(p)]["label"]
            lines.append(f"• {'SELL' if side_of(p) < 0 else 'BUY'} {p.symbol} ({kind}) "
                         f"{p.volume} lot, stop {p.sl:.6g}, P&L {usd(p.profit, True)}")
        closed = [d for d in br.deals(now - pd.Timedelta(days=7)) if d.entry == mt5.DEAL_ENTRY_OUT]
        lines.append(f"\nLast 7 days: {len(closed)} closed, <b>{usd(sum(d.profit for d in closed), True)}</b>")
        if btc_d is not None:
            lines.append("\n" + filter_text(btc_d, now))
        if health.get("gold") and gold_on:
            lines.append("\n" + gold_text(health["gold"], balance))
        return "\n".join(lines)

    def trades():
        ds = [d for d in br.deals(now - pd.Timedelta(days=90)) if d.entry == mt5.DEAL_ENTRY_OUT][-10:]
        if not ds:
            return "🛰️ No closed bot trades yet."
        return "🛰️ <b>Last bot trades</b>\n" + "\n".join(
            f"• {br.utc(d.time):%d %b} {d.symbol}: <b>{usd(d.profit, True)}</b>" for d in ds)

    if live:
        for r in requests_:
            if r["cmd"] == "status":
                if notify(status()) and now.hour >= getattr(config, "MONITOR_DAILY_HOUR", 7):
                    state["summary_day"] = now.strftime("%Y-%m-%d")      # counts as today's summary
            elif r["cmd"] == "trades":
                notify(trades())
            elif r["cmd"] == "run" and not gold_pass:
                notify("✅ Check done. " + (f"{len(events)} update(s) below." if events else
                                           f"Nothing new. {'BTC filter on' if on else 'BTC filter off'}, "
                                           f"{health['open']} open trade(s). /log for details."))
            done.append(r["id"])
        if first_login and creds:
            if notify(f"✅ <b>Logged in to MT5</b> ({login} on {server}).\n\n" + status()):
                state["summary_day"] = now.strftime("%Y-%m-%d")       # that was today's summary
        if events:
            notify("\n\n".join(events))
        elif (env.get("GITHUB_EVENT_NAME") == "schedule" or env.get("HOURLY_CHECK") == "true") \
                and control.get("verbose", True) and not gold_pass:
            gnote = control.get("_gold_note") if control.get("_gold_account") else gold_note
            why = ("paused by you" if control.get("paused") else
                   ("crypto: no breakouts" if on else "crypto: BTC filter off") + f"; gold: {gnote or 'not checked'}")
            notify(f"🕐 {now:%H:%M} UTC check — {health['open']} open trade(s), no new trades ({why}). "
                   f"Balance {usd(balance)}. /quiet to stop these notes.")
        today = now.strftime("%Y-%m-%d")
        if now.hour >= getattr(config, "MONITOR_DAILY_HOUR", 7) and state.get("summary_day") != today:
            if notify("☀️ <b>Daily summary</b>\n\n" + status()):
                state["summary_day"] = today
    return finish(0, f"ok, {entries} new trade(s)")


def _guarded_run(mt5, env, state, control, token, http, what, now=None):
    """run() with crash reporting; returns the exit code."""
    try:
        return run(mt5, env, state, control, http=http, now=now)
    except Exception as e:
        log(f"Run failed ({what}): {type(e).__name__}")
        traceback.print_exc(limit=2)
        state.setdefault("health", {})["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if env.get("MODE", "live") == "live":
            state["result"] = "crashed"
            state["last_run"] = datetime.now(timezone.utc).timestamp()
            chat = env.get("MOMENTUM_TELEGRAM_CHAT_ID") or control.get("tg_chat")
            tg_send(http, token, chat, f"⚠️ Momentum bot run failed ({what}): {type(e).__name__}: {str(e)[:200]}")
        return 1
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def run_accounts(mt5, env, http=requests, runner_path=None, gold_path=None, control_path=None, now=None):
    """One trading run. With a gold account saved (/goldlogin, e.g. an Exness Standard Cent account), gold is
    traded there first and the main account trades crypto only; otherwise the main account trades both."""
    token = env.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    runner_path, gold_path = runner_path or RUNNER_STATE, gold_path or RUNNER_GOLD_STATE
    control = load_json(control_path or CONTROL_STATE, token)
    gcreds = control.get("gold_creds")
    code, gold_note = 0, None
    if gcreds:
        log("Gold account:")
        gstate = load_json(gold_path, token)
        genv = {**env, "BOT_ENGINES": "gold", "BOT_PASS": "gold", "MT5_LOGIN": "", "MT5_PASSWORD": ""}
        code |= _guarded_run(mt5, genv, gstate, {**control, "creds": gcreds}, token, http, "gold account", now)
        save_json(gold_path, gstate, token)
        gold_note = gstate.get("gold_note")
        log("Main account:")
    state = load_json(runner_path, token)
    menv = {**env, "BOT_ENGINES": "crypto" if gcreds else (env.get("BOT_ENGINES") or "crypto,gold")}
    code |= _guarded_run(mt5, menv, state, {**control, "_gold_note": gold_note, "_gold_account": bool(gcreds)},
                         token, http, "main account", now)
    save_json(runner_path, state, token)
    return code


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log("MetaTrader5 package not installed (this runs on the Windows runner).")
        return 1
    return run_accounts(mt5, dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
