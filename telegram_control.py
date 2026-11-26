"""
Telegram listener for the GitHub-hosted momentum bot. Runs every 5 minutes on a small Linux machine:
reads your Telegram messages, answers what it can at once (settings, pause/resume, risk, log, health,
filter, login/logout), and for anything that needs MT5 (status, trades, close, run) it queues the request
and starts a trading run immediately (GitHub workflow_dispatch). Only your chat is answered.

Env: MOMENTUM_TELEGRAM_TOKEN, MOMENTUM_TELEGRAM_CHAT_ID (optional), GITHUB_TOKEN, GITHUB_REPOSITORY,
     DISPATCH_REF (default main). Writes changed=true/false to $GITHUB_OUTPUT so the cache is only saved on change.
"""

import json
import os
import sys
import time

import requests

import config
from cloud_common import (CONTROL_STATE, DEFAULT_SERVER, HELP, RUNNER_GOLD_STATE, RUNNER_STATE, gold_text, load_json,
                          save_json, tg_send)

LIVE_CMDS = {"/status": "status", "/trades": "trades", "/run": "run", "/testtrade": "testtrade"}


def _ago(ts, now):
    if not ts:
        return "never"
    m = int((now - ts) / 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago"


def settings_text(ctl):
    risk = ctl.get("risk_pct") or getattr(config, "MOMENTUM_RISK_PCT", 5.0)
    coins = ", ".join(s.rstrip("m").replace("USD", "") for s in getattr(config, "MOMENTUM_SYMBOLS", []))
    login = ctl.get("creds") or {}
    return ("⚙️ <b>Settings</b>\n"
            f"New trades: <b>{'PAUSED ⏸️' if ctl.get('paused') else 'ON ▶️'}</b>\n"
            f"Crypto risk per trade: <b>{risk:g}%</b> (never above {getattr(config, 'MOMENTUM_MAX_RISK_PCT', 8.0):g}%)\n"
            f"Max open trades: {getattr(config, 'MOMENTUM_MAX_OPEN', 4)}\n"
            f"Hourly check notes: {'on' if ctl.get('verbose', True) else 'off'}\n"
            f"Coins: {coins}\n"
            f"Crypto engines: 1H (max {getattr(config, 'MOMENTUM_1H_RISK_PCT', 2.0):g}% risk) + 4H + Daily breakouts, "
            f"BTC EMA{getattr(config, 'MOMENTUM_REGIME_EMA', 100)} filter\n"
            f"Gold 1H (buy + sell): risk <b>{ctl.get('gold_risk') or getattr(config, 'METALS_RISK_PCT', 2.0):g}%</b> "
            f"(never above {getattr(config, 'METALS_MAX_RISK_PCT', 5.0):g}%) on "
            f"{('gold account ' + str(ctl['gold_creds']['login'])) if ctl.get('gold_creds') else 'the main account'}\n"
            f"MT5 login: {('account ' + str(login.get('login')) + ' on ' + str(login.get('server'))) if login else 'GitHub secrets or not set'}")


def log_text(run, now):
    lines = run.get("log") or []
    if not run.get("last_run"):
        return "📜 No trading run has finished yet."
    return (f"📜 <b>Last run</b> ({_ago(run['last_run'], now)}, {run.get('result', '?')})\n"
            + "\n".join(lines[-40:]))


HOURLY_SECONDS = 55 * 60     # start a trading check when the last one is older than this
AUTO_TEST = 2                # bump to make the listener run /testtrade once more by itself


def health_text(run, ctl, now):
    h = run.get("health") or {}
    age = (now - run["last_run"]) / 3600 if run.get("last_run") else None
    warn = "\n⚠️ No run for over 2 hours — GitHub may be delayed or the workflow is off." if age and age > 2 else ""
    return ("🩺 <b>Health</b>\n"
            f"Last run: {_ago(run.get('last_run'), now)} — {run.get('result', 'none yet')}\n"
            f"MT5 login: {h.get('login', 'unknown')}\n"
            f"Algo Trading: {h.get('algo', 'unknown')}\n"
            f"Orders: {h.get('trading', 'unknown')}\n"
            f"Open trades: {h.get('open', '?')} · Balance: {h.get('balance', '?')}\n"
            + (f"Candle data: {h['data']['ok']}/{h['data']['total']} sets complete and current\n" if h.get("data") else "")
            + "".join(f"  ⚠️ {p}\n" for p in (h.get("data") or {}).get("problems", [])[:5])
            + f"Last error: {h.get('error') or 'none'}\n"
            f"New trades: {'paused' if ctl.get('paused') else 'on'}" + warn)


def filter_text(run, now):
    f = (run.get("health") or {}).get("filter")
    if not f:
        return "🛰️ Filter not known yet — it is read on the first trading run. Send /run."
    on = f["on"]
    return (f"🛰️ <b>Market filter {'ON ✅' if on else 'OFF ⏸️'}</b> (as of {_ago(run.get('last_run'), now)})\n"
            f"BTC {f['btc']:,.0f} vs EMA{getattr(config, 'MOMENTUM_REGIME_EMA', 100)} {f['ema']:,.0f} "
            f"({(f['btc'] / f['ema'] - 1) * 100:+.1f}%)\n"
            + ("Breakouts are being traded." if on else
               f"No new buys until BTC closes above ~{f['ema']:,.0f}."))


def dispatch(http, env, ctl, now):
    """Start a trading run now (at most once a minute; a queued run handles every pending request)."""
    if now - ctl.get("last_dispatch", 0) < 60:
        return True
    repo, gh = env.get("GITHUB_REPOSITORY"), env.get("GITHUB_TOKEN")
    if not repo or not gh:
        return False
    try:
        r = http.post(f"https://api.github.com/repos/{repo}/actions/workflows/cloud-bot.yml/dispatches",
                      headers={"Authorization": f"Bearer {gh}", "Accept": "application/vnd.github+json"},
                      json={"ref": env.get("DISPATCH_REF") or "main", "inputs": {"mode": "live"}}, timeout=15)
        ok = r.status_code in (200, 201, 204)
    except Exception:
        ok = False
    if ok:
        ctl["last_dispatch"] = now
    return ok


def fetch_updates(http, token, ctl, wait=0):
    params = {"timeout": int(wait)}
    if ctl.get("tg_offset"):
        params["offset"] = ctl["tg_offset"]
    try:
        return http.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params, timeout=wait + 15).json()
    except Exception:
        return {}


def handle(http, env, ctl, run, now, updates=None, dispatcher=None, grun=None):
    """Process new Telegram messages. Returns True if control state changed."""
    token = env.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    if not token:
        print("MOMENTUM_TELEGRAM_TOKEN not set.")
        return False
    before = json.dumps(ctl, sort_keys=True)
    # forget requests the trading run has finished
    done = set(run.get("done") or [])
    ctl["requests"] = [r for r in ctl.get("requests", []) if r["id"] not in done]

    if not ctl.get("bot_name"):     # once: which bot this token belongs to (public @username only)
        try:
            me = http.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
            info = http.get(f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=15).json()
            if me.get("ok"):
                ctl["bot_name"] = me["result"].get("username")
                print(f"Listening as @{ctl['bot_name']}; webhook set: {bool((info.get('result') or {}).get('url'))}; "
                      f"pending messages: {(info.get('result') or {}).get('pending_update_count')}")
        except Exception:
            pass
    if updates is None:
        updates = fetch_updates(http, token, ctl)
    chat = str(env.get("MOMENTUM_TELEGRAM_CHAT_ID") or ctl.get("tg_chat")
               or getattr(config, "MOMENTUM_TELEGRAM_CHAT_ID", "") or "") or None
    need_run = False
    if chat and not ctl.get("hello_sent"):
        ok = tg_send(http, token, chat, "👋 <b>Momentum bot connected</b> — I'm running on GitHub now.\n\n"
                     "To start trading, send your MT5 login here:\n"
                     "<code>/login ACCOUNT PASSWORD Exness-MT5Trial9</code>\n"
                     "(I delete that message after reading it.)\n\n" + HELP)
        print(f"Hello message sent: {ok}")
        if ok:
            ctl["hello_sent"] = True
            ctl["tg_chat"] = chat

    def reply(text):
        tg_send(http, token, chat, text)

    def queue(cmd, arg=""):
        ctl["next_id"] = ctl.get("next_id", 0) + 1
        ctl.setdefault("requests", []).append({"id": ctl["next_id"], "cmd": cmd, "arg": arg, "ts": now})

    if updates.get("ok") is False:
        print(f"Telegram refused the token: {updates.get('description', 'unknown error')}")
    if updates.get("result"):
        print(f"Telegram messages received: {len(updates['result'])}")
    if chat and ctl.get("hello_sent") and ctl.get("auto_test_done") != AUTO_TEST:
        # automatic check after setup (bumped when the check improves): a tiny trade opened and closed at once
        ctl["auto_test_done"] = AUTO_TEST
        queue("testtrade")
        need_run = True
        reply("🧪 Running the test trade again: MT5 now starts with Algo Trading forced on, and the bot checks "
              "every permission (Algo Trading, login, broker) and tries other coins and EURUSD. "
              "Result in ~3 minutes.")
    for u in updates.get("result", []):
        ctl["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("edited_message") or {}
        cid = str((msg.get("chat") or {}).get("id") or "")
        text = (msg.get("text") or "").strip()
        if not cid or not text:
            continue
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        if chat is None and cmd in ("/start", "/login"):
            chat = cid
            ctl["tg_chat"] = cid
        if cid != chat:
            continue                                    # never answer anyone else

        if cmd in ("/start", "/help"):
            reply(HELP)
        elif cmd == "/login":
            try:
                http.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                          json={"chat_id": chat, "message_id": msg.get("message_id")}, timeout=15)
            except Exception:
                pass
            if len(parts) >= 3 and parts[1].isdigit():
                ctl["creds"] = {"login": parts[1], "password": parts[2],
                                "server": parts[3] if len(parts) > 3 else DEFAULT_SERVER}
                queue("run")
                need_run = True
                reply(f"✅ MT5 login saved (encrypted) for account {parts[1]} on {ctl['creds']['server']}. "
                      f"Your message was deleted. Logging in now — result in ~3 min.")
            else:
                reply("Send it like this:\n<code>/login 12345678 YourPassword Exness-MT5Trial9</code>")
        elif cmd == "/goldlogin":
            try:
                http.post(f"https://api.telegram.org/bot{token}/deleteMessage",
                          json={"chat_id": chat, "message_id": msg.get("message_id")}, timeout=15)
            except Exception:
                pass
            if len(parts) >= 4 and parts[1].isdigit():
                ctl["gold_creds"] = {"login": parts[1], "password": parts[2], "server": parts[3]}
                queue("run")
                need_run = True
                reply(f"✅ Gold account saved (encrypted): {parts[1]} on {parts[3]}. Your message was deleted.\n"
                      "From now on gold is traded there and crypto stays on your main account. "
                      "Logging in now — result in ~3 min.")
            else:
                reply("Send it like this (server is shown in your Exness Personal Area):\n"
                      "<code>/goldlogin 12345678 YourPassword Exness-MT5Real8</code>")
        elif cmd == "/goldlogout":
            ctl.pop("gold_creds", None)
            reply("🗑️ Gold account removed: gold goes back to your main account.")
        elif cmd == "/logout":
            ctl.pop("creds", None)
            reply("🗑️ Saved MT5 login deleted.")
        elif cmd == "/pause":
            ctl["paused"] = True
            reply("⏸️ Paused: no new trades. Open trades keep their stops and are still managed. /resume to restart.")
        elif cmd == "/resume":
            ctl["paused"] = False
            reply("▶️ Resumed: new breakouts will be traded (crypto while the BTC filter is on; gold both ways).")
        elif cmd == "/risk":
            try:
                v = float(parts[1].rstrip("%"))
                if not 0.5 <= v <= 10:
                    raise ValueError
                ctl["risk_pct"] = v
                reply(f"✅ Risk per trade set to <b>{v:g}%</b> from the next trade.")
            except (IndexError, ValueError):
                reply("Send a number from 0.5 to 10, e.g. <code>/risk 3</code>")
        elif cmd == "/goldrisk":
            try:
                v = float(parts[1].rstrip("%"))
                if not 0.5 <= v <= 8:
                    raise ValueError
                ctl["gold_risk"] = v
                reply(f"✅ Gold risk per trade set to <b>{v:g}%</b> from the next gold trade."
                      + ("\n⚠️ Above 3% the backtest's worst drawdowns were 40–80%. 2% is the tested default."
                         if v > 3 else ""))
            except (IndexError, ValueError):
                reply("Send a number from 0.5 to 8, e.g. <code>/goldrisk 2</code>")
        elif cmd == "/gold":
            src = grun if (ctl.get("gold_creds") and grun) else run
            g = (src.get("health") or {}).get("gold")
            if not g:
                reply("🥇 Gold not checked yet — it is read on the next trading run. Send /run.")
            else:
                try:
                    bal = float(str((src.get("health") or {}).get("balance", "")).lstrip("$"))
                except ValueError:
                    bal = None
                where = "gold account" if src is grun else "main account"
                reply(gold_text(g, bal) + f"\nChecked {_ago(src.get('last_run'), now)} on your {where}.")
        elif cmd in ("/quiet", "/verbose"):
            ctl["verbose"] = cmd == "/verbose"
            reply("🔕 Hourly check notes off (trades, errors and the daily summary still come)." if cmd == "/quiet"
                  else "🔔 Hourly check notes on: you'll hear from me after every hourly check.")
        elif cmd == "/settings":
            reply(settings_text(ctl))
        elif cmd == "/log":
            reply(log_text(run, now))
        elif cmd == "/health":
            reply(health_text(run, ctl, now))
        elif cmd == "/filter":
            reply(filter_text(run, now))
        elif cmd in LIVE_CMDS:
            queue(LIVE_CMDS[cmd])
            need_run = True
            reply("⏳ Checking MT5 now — reply in about 3 minutes.")
        elif cmd == "/close":
            if len(parts) < 2:
                reply("Which one? e.g. <code>/close SOLUSD</code> (or <code>/closeall confirm</code>)")
            else:
                queue("close", parts[1].upper())
                need_run = True
                reply(f"⏳ Closing {parts[1].upper()} — confirmation in about 3 minutes.")
        elif cmd == "/closeall":
            if len(parts) > 1 and parts[1].lower() == "confirm":
                queue("closeall")
                need_run = True
                reply("⏳ Closing all momentum trades — confirmation in about 3 minutes.")
            else:
                reply("This closes every momentum trade at market. Send <code>/closeall confirm</code> to do it.")
        else:
            reply("I don't know that one. /help lists the commands.")

    pending = [r for r in ctl.get("requests", []) if now - r["ts"] < 6 * 3600]
    ctl["requests"] = pending
    if need_run or (pending and now - ctl.get("last_dispatch", 0) > 15 * 60):
        print(f"Requests waiting for a trading run: {len(pending)}")
        if (dispatcher or (lambda: dispatch(http, env, ctl, now)))():
            print("Trading run started.")
        else:
            print("Could not start a trading run.")
            reply("⚠️ Couldn't start a run right now; it will happen at the next hourly check.")
    return json.dumps(ctl, sort_keys=True) != before


def main():
    """Listen for up to LISTEN_SECONDS (long polling, replies within seconds). If a trading run is needed, stop
    early so the workflow saves the settings first and then starts cloud-bot.yml (outputs: changed, dispatch)."""
    env = dict(os.environ)
    token = env.get("MOMENTUM_TELEGRAM_TOKEN") or ""
    ctl = load_json(CONTROL_STATE, token)
    run = load_json(RUNNER_STATE, token)
    grun = load_json(RUNNER_GOLD_STATE, token)
    deadline = time.time() + float(env.get("LISTEN_SECONDS") or 0)
    wanted = []

    def record_dispatch():
        if time.time() - ctl.get("last_dispatch", 0) < 60:
            return True
        wanted.append(True)
        ctl["last_dispatch"] = time.time()
        return True

    changed = False
    hourly = False
    # GitHub's cron is unreliable, so the always-on listener also starts the hourly trading check itself
    if token and time.time() - float(run.get("last_run") or 0) > HOURLY_SECONDS \
            and time.time() - ctl.get("last_dispatch", 0) > 900:
        record_dispatch()
        hourly = changed = True
    while True:
        wait = max(0, min(25, deadline - time.time()))
        updates = fetch_updates(requests, token, ctl, wait) if token else {}
        changed |= handle(requests, env, ctl, run, time.time(), updates=updates, dispatcher=record_dispatch, grun=grun)
        if wanted or time.time() >= deadline or not token:
            break
    if changed:
        save_json(CONTROL_STATE, ctl, token)
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\ndispatch={'true' if wanted else 'false'}\n"
                    f"hourly={'true' if hourly else 'false'}\n")
    print(f"Telegram check done ({'state changed' if changed else 'no changes'}"
          f"{', starting the hourly check' if hourly else ', starting a trading run' if wanted else ''}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
