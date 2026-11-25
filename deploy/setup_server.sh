#!/usr/bin/env bash
# One-command setup of the FXAlexG bot on a fresh Ubuntu 22.04 / 24.04 server (e.g. a Google Cloud e2-micro).
#
# On the server:
#   wget -qO setup.sh https://raw.githubusercontent.com/anonyemichael/FXAlexG-TradingBot/ccr-40ef48ac-4webyc/deploy/setup_server.sh
#   bash setup.sh
#
# It asks for your MT5 login/password/server and your main Telegram bot token, then installs, unattended:
#   swap (1 GB RAM servers need it), Wine + a virtual screen, Exness MetaTrader 5, Windows Python 3.9 +
#   MetaTrader5 package (for mt5_bridge.py), the bot code + its Python packages, Algo Trading switched on,
#   systemd services xvfb / mt5-bridge / fxalexg-bot (start on boot, restart on crash),
#   and an SSH key for GitHub Actions deploys (printed at the end with the GitHub secrets to add).
# Safe to run again: finished steps are skipped and an existing .env is kept.
#
# Non-interactive (used by the CI test): NONINTERACTIVE=1 MT5_LOGIN=.. MT5_PASSWORD=.. MT5_SERVER=.. TELEGRAM_TOKEN=..
# START_SERVICES=0 installs everything but doesn't start the bridge/bot.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/anonyemichael/FXAlexG-TradingBot.git}
BRANCH=${BRANCH:-ccr-40ef48ac-4webyc}
BOT_DIR=${BOT_DIR:-$HOME/fxalexg_bot}
PREFIX=${WINE_PREFIX_DIR:-$HOME/.wine_exness}
DISP=${XVFB_DISPLAY:-:99}
START_SERVICES=${START_SERVICES:-1}
PY_WIN_URL=https://www.python.org/ftp/python/3.9.13/python-3.9.13-amd64.exe
MT5_URLS="https://download.mql5.com/cdn/web/exness.technologies.ltd/mt5/exness5setup.exe
https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

log() { printf '\n\033[1;36m== [%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not root (it uses sudo where needed)."
sudo -n true 2>/dev/null || die "Your user needs passwordless sudo (the default on Google Cloud)."
. /etc/os-release
[ "${ID:-}" = ubuntu ] || die "This script needs Ubuntu 22.04 or 24.04 (found ${PRETTY_NAME:-unknown})."
CODENAME=$VERSION_CODENAME
[ "$(uname -m)" = x86_64 ] || die "Needs an x86_64 (Intel/AMD) server; MT5 doesn't run on ARM servers."

# ── 1. Questions first, so the long install runs unattended ─────────────────
ask() {   # ask VAR "prompt" [secret] [default]
  local var=$1 prompt=$2 secret=${3:-} def=${4:-} val
  if [ -n "${!var:-}" ]; then return; fi
  if [ "${NONINTERACTIVE:-0}" = 1 ]; then
    [ -n "$def" ] && { printf -v "$var" '%s' "$def"; return; }
    die "$var is not set"
  fi
  if [ -n "$secret" ]; then read -r -s -p "$prompt: " val; echo; else read -r -p "$prompt${def:+ [$def]}: " val; fi
  printf -v "$var" '%s' "${val:-$def}"
}
if [ -f "$BOT_DIR/.env" ] && [ "${FORCE_ENV:-0}" != 1 ]; then
  log "Keeping your existing $BOT_DIR/.env"
  WRITE_ENV=0
else
  WRITE_ENV=1
  echo "Your answers are saved only in $BOT_DIR/.env on this server (readable by you only)."
  ask MT5_LOGIN "MT5 account number"
  ask MT5_PASSWORD "MT5 password (typing is hidden)" secret
  ask MT5_SERVER "MT5 server" "" "Exness-MT5Trial9"
  ask ACCOUNT_TYPE "Is it a demo or real account? (demo/real)" "" "demo"
  ask TELEGRAM_TOKEN "Main Telegram bot token (BotFather -> /mybots -> your main bot -> API Token; hidden)" secret
  ask TELEGRAM_CHAT_ID "Your Telegram chat id (Enter to skip: send /start to the main bot later)" "" "-"
  [[ "$MT5_LOGIN" =~ ^[0-9]+$ ]] || die "MT5 account number must be digits only"
fi

# ── 2. Swap: a 1 GB server can't hold MT5 + Wine + Python without it ────────
if [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 3000 ] && [ -z "$(swapon --show --noheadings)" ]; then
  log "Adding 2 GB swap"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ── 3. System packages + WineHQ ──────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
if ! command -v wine >/dev/null 2>&1; then
  log "Installing Wine and system packages (5-10 minutes)"
  sudo dpkg --add-architecture i386
  sudo apt-get update -qq
  sudo apt-get install -y -qq --no-install-recommends ca-certificates curl wget git xvfb xauth \
    python3 python3-venv python3-pip cabextract unzip >/dev/null
  sudo mkdir -pm755 /etc/apt/keyrings
  sudo wget -qO /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
  sudo wget -qNP /etc/apt/sources.list.d/ "https://dl.winehq.org/wine-builds/ubuntu/dists/$CODENAME/winehq-$CODENAME.sources"
  sudo apt-get update -qq
  sudo apt-get install -y -qq --install-recommends winehq-stable >/dev/null
fi
wine --version

# ── 4. Bot code + Linux Python packages ──────────────────────────────────────
log "Bot code ($BRANCH) in $BOT_DIR"
if [ -d "$BOT_DIR/.git" ]; then
  git -C "$BOT_DIR" fetch -q origin "$BRANCH" && git -C "$BOT_DIR" checkout -q "$BRANCH" && git -C "$BOT_DIR" pull -q --ff-only || true
elif [ -d "$BOT_DIR" ] && [ -n "$(ls -A "$BOT_DIR")" ]; then
  log "$BOT_DIR already has files and isn't a git checkout; leaving the code as it is"
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$BOT_DIR"
fi
[ -x "$BOT_DIR/.venv/bin/python" ] || python3 -m venv "$BOT_DIR/.venv"
"$BOT_DIR/.venv/bin/pip" install -q --upgrade pip
"$BOT_DIR/.venv/bin/pip" install -q pandas numpy requests

if [ "$WRITE_ENV" = 1 ]; then
  log "Writing $BOT_DIR/.env"
  ( umask 077
    {
      echo "TELEGRAM_TOKEN=$TELEGRAM_TOKEN"
      [ "$TELEGRAM_CHAT_ID" != "-" ] && echo "TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID"
      echo "BRIDGE_URL=http://127.0.0.1:5001"
      echo "MT5_DEMO_LOGIN=$MT5_LOGIN"
      echo "MT5_DEMO_PASSWORD=$MT5_PASSWORD"
      echo "MT5_DEMO_SERVER=$MT5_SERVER"
      if [ "$ACCOUNT_TYPE" = real ]; then
        echo "MT5_REAL_LOGIN=$MT5_LOGIN"; echo "MT5_REAL_PASSWORD=$MT5_PASSWORD"; echo "MT5_REAL_SERVER=$MT5_SERVER"
        echo "ACTIVE_ACCOUNT=real"
      else
        echo "ACTIVE_ACCOUNT=demo"
      fi
      echo "AI_ENABLED=false"
    } > "$BOT_DIR/.env" )
fi

# ── 5. Virtual screen (MT5 is a Windows GUI program) ────────────────────────
log "Virtual screen service (xvfb on $DISP)"
sudo tee /etc/systemd/system/xvfb.service >/dev/null <<EOF
[Unit]
Description=Virtual display for MetaTrader 5
After=network.target

[Service]
ExecStart=/usr/bin/Xvfb $DISP -screen 0 1280x1024x24 -nolisten tcp
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb.service >/dev/null 2>&1
sleep 2

export WINEPREFIX="$PREFIX" WINEARCH=win64 WINEDEBUG=-all DISPLAY="$DISP" WINEDLLOVERRIDES="mscoree,mshtml="
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── 6. Wine prefix (Windows 10 mode) ─────────────────────────────────────────
if [ ! -f "$PREFIX/system.reg" ]; then
  log "Creating the Wine environment in $PREFIX"
  timeout 300 wineboot --init >/dev/null 2>&1 || true
  timeout 120 wineserver -w || true
  timeout 120 wine winecfg -v win10 >/dev/null 2>&1 || true
  timeout 120 wineserver -w || true
fi

# ── 7. MetaTrader 5 ──────────────────────────────────────────────────────────
find_terminal() { find "$PREFIX/drive_c" -iname terminal64.exe 2>/dev/null | head -n1; }
TERM_EXE=$(find_terminal)
if [ -z "$TERM_EXE" ]; then
  log "Installing MetaTrader 5 (downloads ~100 MB)"
  for u in $MT5_URLS; do
    if wget -qO "$TMP/mt5setup.exe" "$u"; then echo "Downloaded $u"; break; fi
  done
  [ -s "$TMP/mt5setup.exe" ] || die "Could not download the MT5 installer"
  timeout 900 wine "$TMP/mt5setup.exe" /auto >/dev/null 2>&1 &
  INST=$!
  for _ in $(seq 1 90); do
    TERM_EXE=$(find_terminal)
    if [ -n "$TERM_EXE" ] && ! kill -0 "$INST" 2>/dev/null; then break; fi
    sleep 10
  done
  sleep 20
  wineserver -k || true
  TERM_EXE=$(find_terminal)
  [ -n "$TERM_EXE" ] || die "MT5 did not install (terminal64.exe not found)"
fi
TERM_DIR=$(dirname "$TERM_EXE")
echo "MT5: $TERM_DIR"
# mt5_bridge.py looks in "C:\Program Files\MetaTrader 5" (or "...EXNESS"): point that name at the real folder
STD="$PREFIX/drive_c/Program Files/MetaTrader 5"
if [ ! -e "$STD" ] && [ ! -e "$STD EXNESS" ]; then ln -s "$TERM_DIR" "$STD"; fi

# ── 8. Windows Python 3.9 + MetaTrader5 package (runs mt5_bridge.py) ────────
WINPY="$PREFIX/drive_c/Program Files/Python39/python.exe"
if [ ! -f "$WINPY" ]; then
  log "Installing Windows Python 3.9 inside Wine"
  wget -qO "$TMP/python.exe" "$PY_WIN_URL"
  timeout 900 wine "$TMP/python.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0 Include_tcltk=0 >/dev/null 2>&1 || true
  timeout 120 wineserver -w || true
  [ -f "$WINPY" ] || die "Windows Python did not install"
fi
log "Installing the MetaTrader5 Python package"
timeout 600 wine "$WINPY" -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
timeout 900 wine "$WINPY" -m pip install -q --disable-pip-version-check MetaTrader5 >/dev/null 2>&1 || true
wine "$WINPY" -c "import MetaTrader5 as m; print('MetaTrader5 package', m.__version__)" 2>/dev/null | tr -d '\r' \
  || die "MetaTrader5 package not importable in Wine Python"

# ── 9. Switch on Algo Trading (the bridge refuses to trade without it) ──────
log "Starting MT5 once to create its settings, then enabling Algo Trading"
timeout 120 wine "$TERM_EXE" >/dev/null 2>&1 &   # same (non-portable) data folder the bridge will use
sleep 60
wineserver -k || true
sleep 3
python3 - "$TERM_DIR" "$PREFIX" <<'PY'
import glob, os, sys
term_dir, prefix = sys.argv[1], sys.argv[2]
paths = set(glob.glob(os.path.join(term_dir, "Config", "common.ini")))
paths |= set(glob.glob(os.path.join(prefix, "drive_c", "users", "*", "AppData", "Roaming", "MetaQuotes", "Terminal", "*", "config", "common.ini")))
if not paths:
    os.makedirs(os.path.join(term_dir, "Config"), exist_ok=True)
    paths = {os.path.join(term_dir, "Config", "common.ini")}
want = {"Enabled": "1", "AllowLiveTrading": "1", "Account": "0", "Profile": "0"}
for p in paths:
    raw = open(p, "rb").read() if os.path.exists(p) else b""
    utf16 = raw[:2] == b"\xff\xfe"
    text = raw[2:].decode("utf-16-le") if utf16 else raw.decode("utf-8", "replace")
    lines, out, in_exp, seen = text.splitlines(), [], False, set()
    for ln in lines:
        s = ln.strip()
        if s.startswith("["):
            if in_exp:
                out += [f"{k}={v}" for k, v in want.items() if k not in seen]
            in_exp = s.lower() == "[experts]"
            out.append(ln); continue
        if in_exp and "=" in s and s.split("=", 1)[0] in want:
            k = s.split("=", 1)[0]; seen.add(k); out.append(f"{k}={want[k]}"); continue
        out.append(ln)
    if in_exp:
        out += [f"{k}={v}" for k, v in want.items() if k not in seen]
    if not any(l.strip().lower() == "[experts]" for l in out):
        out += ["[Experts]"] + [f"{k}={v}" for k, v in want.items()]
    body = "\r\n".join(out) + "\r\n"
    open(p, "wb").write(b"\xff\xfe" + body.encode("utf-16-le"))
    print("Algo Trading enabled in", p)
PY

# ── 10. Services ─────────────────────────────────────────────────────────────
log "Installing services mt5-bridge and fxalexg-bot"
BRIDGE_WIN="Z:${BOT_DIR}/mt5_bridge.py"
sudo tee /etc/systemd/system/mt5-bridge.service >/dev/null <<EOF
[Unit]
Description=MT5 bridge (MetaTrader 5 under Wine, REST on 127.0.0.1:5001)
After=network-online.target xvfb.service
Requires=xvfb.service

[Service]
User=$USER
WorkingDirectory=$BOT_DIR
Environment=DISPLAY=$DISP
Environment=WINEPREFIX=$PREFIX
Environment=WINEDEBUG=-all
ExecStart=/usr/bin/wine "$WINPY" "$BRIDGE_WIN"
ExecStopPost=/usr/bin/env WINEPREFIX=$PREFIX /usr/bin/wineserver -k
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
sudo tee /etc/systemd/system/fxalexg-bot.service >/dev/null <<EOF
[Unit]
Description=FXAlexG trading bot
After=network-online.target mt5-bridge.service

[Service]
User=$USER
WorkingDirectory=$BOT_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$BOT_DIR/.venv/bin/python -u bot.py
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable mt5-bridge.service fxalexg-bot.service >/dev/null 2>&1

# ── 11. SSH key for GitHub Actions deploys ───────────────────────────────────
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
[ -f "$HOME/.ssh/github_deploy" ] || ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/github_deploy" -C github-actions-deploy
touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys"
grep -qF "$(cat "$HOME/.ssh/github_deploy.pub")" "$HOME/.ssh/authorized_keys" || cat "$HOME/.ssh/github_deploy.pub" >> "$HOME/.ssh/authorized_keys"

# ── 12. Start and check ──────────────────────────────────────────────────────
if [ "$START_SERVICES" = 1 ]; then
  log "Starting MT5 bridge (first login can take 1-2 minutes)"
  sudo systemctl restart mt5-bridge.service
  ok=0
  for _ in $(seq 1 24); do
    if curl -s -m 5 http://127.0.0.1:5001/health | grep -q '"connected": true'; then ok=1; break; fi
    sleep 10
  done
  if [ "$ok" = 1 ]; then
    echo "✅ MT5 bridge is connected to your broker account."
  else
    echo "⚠️  The bridge isn't connected yet. Check:  sudo journalctl -u mt5-bridge -n 50"
    echo "    (wrong MT5 login/password/server is the usual cause: fix $BOT_DIR/.env, then: sudo systemctl restart mt5-bridge)"
  fi
  sudo systemctl restart fxalexg-bot.service
  sleep 5
  systemctl is-active --quiet fxalexg-bot && echo "✅ Bot service is running." || echo "⚠️  Bot service not running: sudo journalctl -u fxalexg-bot -n 50"
fi

IP=$(curl -s -m 3 -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip" 2>/dev/null || true)
[ -n "$IP" ] || IP="<this server's external IP>"
cat <<EOF

────────────────────────────────────────────────────────────────────────
 Done. Last steps (in Telegram and on GitHub):

 1. Send /start to your MAIN Telegram bot (if you skipped the chat id).

 2. GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
      VPS_HOST      = $IP
      VPS_USER      = $USER
      BOT_DIR       = $BOT_DIR
      VPS_SSH_KEY   = the key printed below (copy every line, BEGIN to END)

 3. GitHub -> Actions -> latest "Test and deploy to VPS" run -> Re-run all jobs.
    That copies the latest code here, adds your momentum monitor bot, and restarts.

 4. Send /start to your MOMENTUM monitor bot.

 Useful:  sudo journalctl -u fxalexg-bot -f      (live bot log)
          sudo systemctl restart mt5-bridge fxalexg-bot
────────────────────────────────────────────────────────────────────────
 VPS_SSH_KEY (private key, keep it secret — paste only into the GitHub secret):
EOF
cat "$HOME/.ssh/github_deploy"
