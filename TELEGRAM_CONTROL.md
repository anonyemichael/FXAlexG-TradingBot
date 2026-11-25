# Run the momentum bot from Telegram (no VPS, no logs to read)

The bot runs on GitHub. Two parts:
- **Trading run** (`cloud-bot.yml`): every hour, or right away when you ask. Logs in to MT5, manages trades,
  buys new breakouts, reports to Telegram.
- **Telegram listener** (`telegram-control.yml`): every 5 minutes. Answers your commands.

Only one GitHub secret is needed: `MOMENTUM_TELEGRAM_TOKEN` (your bot's token from @BotFather).
Your MT5 login is sent in Telegram with `/login` and stored encrypted. Settings, logs and the login are kept in
GitHub's cache, encrypted with your bot token, so nothing readable is stored in this public repository.

## Start
1. Send `/start` to your bot (the first chat that does becomes the only one it answers).
2. Send `/login ACCOUNT PASSWORD SERVER`, e.g. `/login 12345678 MyPass Exness-MT5Trial9`.
   The bot deletes that message, logs in within a few minutes and replies with your balance.

## Commands
| Command | What it does | Reply time |
|---|---|---|
| `/help` | list of commands | ~5 min |
| `/settings` | risk, coins, paused or not, login in use | ~5 min |
| `/pause` · `/resume` | stop / allow new trades (open trades keep being managed) | ~5 min |
| `/risk 3` | risk per trade in % (0.5–10; the 8% per-trade cap still applies) | ~5 min |
| `/log` | what the last trading run did, line by line | ~5 min |
| `/health` | last run, MT5 login, Algo Trading, last error | ~5 min |
| `/filter` | Bitcoin trend filter (on = trading allowed) | ~5 min |
| `/status` | live balance, open trades, P&L | ~8 min |
| `/trades` | last 10 closed trades | ~8 min |
| `/run` | check for new trades now | ~8 min |
| `/close SOLUSD` | close one trade at market | ~8 min |
| `/closeall confirm` | close every momentum trade at market | ~8 min |
| `/login …` · `/logout` | save / delete the MT5 login | ~5 min |

You also get, without asking: every buy, stop raise and close, filter on/off changes, errors, and a daily
summary at 07:00 UTC. GitHub's timers can run a few minutes late.
