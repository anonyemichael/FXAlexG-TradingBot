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
| `/help` | list of commands | seconds to ~1 min |
| `/settings` | risk, coins, paused or not, login in use | seconds to ~1 min |
| `/pause` · `/resume` | stop / allow new trades (open trades keep being managed) | seconds to ~1 min |
| `/risk 3` | risk per trade in % (0.5–10; the 8% per-trade cap still applies) | seconds to ~1 min |
| `/log` | what the last trading run did, line by line | seconds to ~1 min |
| `/health` | last run, MT5 login, Algo Trading, last error | seconds to ~1 min |
| `/filter` | Bitcoin trend filter (on = trading allowed) | seconds to ~1 min |
| `/quiet` · `/verbose` | turn the hourly "checked, nothing new" note off / on | seconds to ~1 min |
| `/status` | live balance, open trades, P&L | ~3 min |
| `/trades` | last 10 closed trades | ~3 min |
| `/run` | check for new trades now | ~3 min |
| `/testtrade` | smallest possible trade on the cheapest coin, opened and closed at once (proves orders reach MT5; costs a few cents of spread) | ~3 min |
| `/close SOLUSD` | close one trade at market | ~3 min |
| `/closeall confirm` | close every momentum trade at market | ~3 min |
| `/login …` · `/logout` | save / delete the MT5 login | seconds to ~1 min |

You also get, without asking: every buy (with the reason: breakout level, Bitcoin filter, stop distance),
every stop raise and close with P&L, filter on/off changes, errors, a one-line note after each hourly check
(`/quiet` turns it off) and a daily summary at 07:00 UTC. One test trade runs automatically after setup. GitHub's timers can run a few minutes late.
