#!/bin/bash
# Google Cloud VM startup script: the server sets itself up and keeps itself updated. No SSH, no GitHub secrets.
#
# When creating the VM (Advanced options -> Management):
#   Metadata, "Add item" for each (key = value):
#     MT5_LOGIN        your MT5 account number
#     MT5_PASSWORD     your MT5 password
#     MT5_SERVER       e.g. Exness-MT5Trial9
#     TELEGRAM_TOKEN   your main Telegram bot token
#     MOMENTUM_TELEGRAM_TOKEN   (optional) the separate momentum monitor bot
#     TELEGRAM_CHAT_ID / ACCOUNT_TYPE (demo|real) / BRANCH   (optional)
#   Automation -> Startup script:
#     curl -fsSL https://raw.githubusercontent.com/anonyemichael/FXAlexG-TradingBot/ccr-40ef48ac-4webyc/deploy/gcp_startup.sh | bash
#
# Runs as root at every boot. First boot installs everything (15-30 min on an e2-micro); later boots only refresh
# .env from the metadata (so you can change details in the console and press Reset) and restart the services.
# A timer checks GitHub every 15 minutes: new code is tested, deployed, and rolled back if the bot doesn't start.
# Progress: VM page -> Logs -> Serial port 1, or Telegram once the bot is up.
set -uo pipefail

BOT_USER=trader
BOT_HOME=/home/$BOT_USER
BOT_DIR=$BOT_HOME/fxalexg_bot
RAW=https://raw.githubusercontent.com/anonyemichael/FXAlexG-TradingBot

say() { echo "[fxalexg-startup $(date -u +%H:%M:%S)] $*"; }

md() {  # metadata value (META_<KEY> env var overrides, used by the CI test)
  local env="META_$1"
  if [ -n "${!env:-}" ]; then printf '%s' "${!env}"; return; fi
  curl -fs -m 5 -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null || true
}

BRANCH=$(md BRANCH); BRANCH=${BRANCH:-ccr-40ef48ac-4webyc}
MT5_LOGIN=$(md MT5_LOGIN); MT5_PASSWORD=$(md MT5_PASSWORD); MT5_SERVER=$(md MT5_SERVER)
TELEGRAM_TOKEN=$(md TELEGRAM_TOKEN); TELEGRAM_CHAT_ID=$(md TELEGRAM_CHAT_ID)
ACCOUNT_TYPE=$(md ACCOUNT_TYPE); MOMENTUM_TELEGRAM_TOKEN=$(md MOMENTUM_TELEGRAM_TOKEN)

missing=""
for k in MT5_LOGIN MT5_PASSWORD MT5_SERVER TELEGRAM_TOKEN; do [ -n "${!k}" ] || missing="$missing $k"; done
if [ -n "$missing" ]; then
  say "Missing metadata:$missing. Add them (VM -> Edit -> Metadata), save, then press Reset on the VM."
  exit 0
fi

# 1. a normal user with sudo (setup_server.sh refuses to run as root)
if ! id "$BOT_USER" >/dev/null 2>&1; then
  say "Creating user $BOT_USER"
  useradd -m -s /bin/bash "$BOT_USER"
fi
echo "$BOT_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$BOT_USER && chmod 440 /etc/sudoers.d/90-$BOT_USER

# 2. run the installer as that user; credentials go through a private file, never the command line
CRED=$(mktemp -p "$BOT_HOME" .setup-env.XXXX)
chown "$BOT_USER:" "$CRED" && chmod 600 "$CRED"
{
  printf 'NONINTERACTIVE=1\nFORCE_ENV=1\nSKIP_DEPLOY_KEY=1\nBRANCH=%q\n' "$BRANCH"
  printf 'MT5_LOGIN=%q\nMT5_PASSWORD=%q\nMT5_SERVER=%q\n' "$MT5_LOGIN" "$MT5_PASSWORD" "$MT5_SERVER"
  printf 'TELEGRAM_TOKEN=%q\nTELEGRAM_CHAT_ID=%q\nACCOUNT_TYPE=%q\n' "$TELEGRAM_TOKEN" "${TELEGRAM_CHAT_ID:--}" "${ACCOUNT_TYPE:-demo}"
} > "$CRED"
SETUP=${SETUP_SCRIPT_PATH:-}
if [ -z "$SETUP" ]; then
  SETUP=$(mktemp -p "$BOT_HOME" setup.XXXX.sh)
  curl -fsSL "$RAW/$BRANCH/deploy/setup_server.sh" -o "$SETUP" || { say "Could not download setup_server.sh"; rm -f "$CRED"; exit 1; }
fi
chown "$BOT_USER:" "$SETUP" 2>/dev/null; chmod 755 "$SETUP"
say "Running setup as $BOT_USER (first boot: 15-30 minutes)"
sudo -u "$BOT_USER" -H bash -c "set -a; . '$CRED'; set +a; bash '$SETUP'" 2>&1 | sed 's/^/[setup] /'
status=${PIPESTATUS[0]}
rm -f "$CRED"
[ -z "${SETUP_SCRIPT_PATH:-}" ] && rm -f "$SETUP"
[ "$status" = 0 ] || { say "Setup failed (exit $status). See the lines above."; exit 1; }

# 3. momentum monitor bot token (optional)
( umask 077
  { [ -n "$MOMENTUM_TELEGRAM_TOKEN" ] && echo "MOMENTUM_TELEGRAM_TOKEN=$MOMENTUM_TELEGRAM_TOKEN"
    echo "DEPLOY_COMMIT=$(git -C "$BOT_DIR" rev-parse HEAD 2>/dev/null)"; } > "$BOT_DIR/.env.monitor" )
chown "$BOT_USER:" "$BOT_DIR/.env.monitor"

# 4. auto-update from GitHub every 15 minutes (test -> deploy -> health check -> rollback)
cat > /usr/local/bin/fxalexg-update <<'UPD'
#!/bin/bash
# Pull new bot code from GitHub; test it, restart, roll back if the bot doesn't stay up.
set -uo pipefail
BOT_DIR=$HOME/fxalexg_bot
cd "$BOT_DIR" || exit 1
BRANCH=$(git rev-parse --abbrev-ref HEAD); [ "$BRANCH" = HEAD ] && BRANCH=$(cat .deploy_branch 2>/dev/null || echo main)
git fetch -q origin "$BRANCH" || exit 0
OLD=$(git rev-parse HEAD); NEW=$(git rev-parse "origin/$BRANCH")
[ "$OLD" = "$NEW" ] && exit 0
echo "Update $OLD -> $NEW"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
git archive "$NEW" | tar x -C "$T"
( cd "$T" && "$BOT_DIR/.venv/bin/python" test_momentum.py >/dev/null && "$BOT_DIR/.venv/bin/python" test_monitor.py >/dev/null ) \
  || { echo "Tests failed for $NEW — not deploying"; exit 1; }
bridge_changed=$(git diff --name-only "$OLD" "$NEW" -- mt5_bridge.py)
git reset -q --hard "$NEW"
sed -i "s/^DEPLOY_COMMIT=.*/DEPLOY_COMMIT=$NEW/" .env.monitor 2>/dev/null || echo "DEPLOY_COMMIT=$NEW" >> .env.monitor
[ -n "$bridge_changed" ] && sudo systemctl restart mt5-bridge
before=$(systemctl show -p NRestarts --value fxalexg-bot)
sudo systemctl restart fxalexg-bot
sleep 45
after=$(systemctl show -p NRestarts --value fxalexg-bot)
if systemctl is-active --quiet fxalexg-bot && [ "$before" = "$after" ]; then
  echo "Deployed $NEW"
else
  echo "Bot did not start cleanly on $NEW — rolling back to $OLD"
  git reset -q --hard "$OLD"
  sed -i "s/^DEPLOY_COMMIT=.*/DEPLOY_COMMIT=$OLD/" .env.monitor 2>/dev/null
  [ -n "$bridge_changed" ] && sudo systemctl restart mt5-bridge
  sudo systemctl restart fxalexg-bot
  exit 1
fi
UPD
chmod 755 /usr/local/bin/fxalexg-update
echo "$BRANCH" > "$BOT_DIR/.deploy_branch" && chown "$BOT_USER:" "$BOT_DIR/.deploy_branch"
cat > /etc/systemd/system/fxalexg-update.service <<EOF
[Unit]
Description=Pull and deploy new FXAlexG bot code from GitHub
After=network-online.target

[Service]
Type=oneshot
User=$BOT_USER
ExecStart=/usr/local/bin/fxalexg-update
EOF
cat > /etc/systemd/system/fxalexg-update.timer <<'EOF'
[Unit]
Description=Check GitHub for FXAlexG bot updates every 15 minutes

[Timer]
OnBootSec=10min
OnUnitActiveSec=15min
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now fxalexg-update.timer >/dev/null 2>&1
say "Done. Bot running as $BOT_USER; updates every 15 min. Send /start to your Telegram bot(s)."
