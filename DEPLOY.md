# Automatic deploy (GitHub Actions) + separate momentum Telegram bot

Every push to `main` or `ccr-40ef48ac-4webyc` runs `.github/workflows/deploy.yml`:
1. **Tests** (Python 3.10 and 3.12): compile all files, `test_momentum.py`, `test_monitor.py`, `test_swing_alexg.py`.
2. **Deploy** (only once the secrets below exist): back up the bot folder on the VPS, copy the repo's
   `*.py` / `*.md` files (never `.env`, databases, chat/account state or `mt5_bridge.py`), write `.env.monitor`,
   restart `fxalexg-bot`, and roll back automatically if it doesn't come up cleanly within 40 seconds.

You can also run it by hand: GitHub → **Actions** → *Test and deploy to VPS* → **Run workflow**
(that button appears once this file is on the default branch; until then a push to the branch runs it).

## One-time setup

### 1. VPS: make sure `.env` holds your credentials
The deploy refuses to run unless `/home/ubuntu/fxalexg_bot/.env` contains `TELEGRAM_TOKEN=` and
`MT5_DEMO_LOGIN=` (or `MT5_REAL_LOGIN=`). If your VPS `config.py` still has them typed in directly, copy them
into `.env` first (template: `.env.example`), because the deployed `config.py` reads them from `.env`.

### 2. An SSH key for GitHub
On the VPS:
```
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_deploy            # private key -> secret VPS_SSH_KEY
ssh-keyscan -H localhost            # optional; or run `ssh-keyscan -H <public IP>` from your PC -> VPS_KNOWN_HOSTS
```

### 3. The momentum monitor bot
In Telegram, open **@BotFather** → `/newbot` → copy the token. Don't paste it into a chat; it goes into GitHub only.

### 4. GitHub secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `VPS_HOST` | VPS public IP |
| `VPS_SSH_KEY` | the private key from step 2 (whole file) |
| `MOMENTUM_TELEGRAM_TOKEN` | the token from step 3 |
| `VPS_USER`, `VPS_PORT`, `BOT_DIR`, `BOT_SERVICE`, `VPS_KNOWN_HOSTS`, `MOMENTUM_TELEGRAM_CHAT_ID` | optional (defaults: `ubuntu`, `22`, `/home/ubuntu/fxalexg_bot`, `fxalexg-bot`) |

Then GitHub → **Actions** → open the latest run → **Re-run all jobs**.

### 5. Claim the monitor bot
Send `/start` to the new bot. The first chat that does becomes its owner (others are ignored), unless you set
`MOMENTUM_TELEGRAM_CHAT_ID`. It then receives every momentum message and answers:
`/status` (accounts, open trades, P&L) · `/trades` (last 10) · `/filter` (BTC vs its 100-day EMA),
plus a daily summary at 07:00 UTC. All other bots' messages stay on your main Telegram bot.

## Rolling back by hand
Each deploy leaves `/home/ubuntu/fxalexg_bot_deploy_backup_<time>` (last 5 kept):
```
cp -a /home/ubuntu/fxalexg_bot_deploy_backup_<time>/*.py /home/ubuntu/fxalexg_bot/
sudo systemctl restart fxalexg-bot
```
