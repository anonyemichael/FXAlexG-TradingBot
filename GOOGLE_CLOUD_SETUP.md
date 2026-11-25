# Run the bot on a free Google Cloud server (about 30 minutes, once)

## 1. Create the free server
1. Open **https://cloud.google.com/free** → **Get started for free** and sign in with your Google account.
   Google asks for a card to verify you; the e2-micro server below is in the "always free" tier.
2. Open **https://console.cloud.google.com/compute/instancesAdd** (if it asks, click **Enable** for Compute Engine).
3. Fill in:
   - **Name:** `fxalexg-bot`
   - **Region:** `us-central1`, `us-east1` or `us-west1` (only these 3 are free)
   - **Machine type:** series **E2**, type **e2-micro**
   - **Boot disk → Change:** Operating system **Ubuntu**, version **Ubuntu 22.04 LTS (x86/64)**,
     disk type **Standard persistent disk**, size **30 GB** → **Select**
4. Click **Create**. Wait for the green tick.

## 2. Install the bot (one paste)
1. In the VM list, click **SSH** next to `fxalexg-bot`. A terminal opens in your browser.
2. Paste this and press Enter:
   ```
   wget -qO setup.sh https://raw.githubusercontent.com/anonyemichael/FXAlexG-TradingBot/ccr-40ef48ac-4webyc/deploy/setup_server.sh && bash setup.sh
   ```
3. Answer the questions: MT5 account number, MT5 password, MT5 server (e.g. `Exness-MT5Trial9`),
   demo or real, your **main** Telegram bot token, and your chat id (Enter to skip).
   Then wait 15–25 minutes. Don't close the tab.
4. At the end it prints ✅ lines and **the 4 GitHub secrets to add** (VPS_HOST, VPS_USER, BOT_DIR, VPS_SSH_KEY).

## 3. Connect GitHub (automatic updates)
1. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:
   add `VPS_HOST`, `VPS_USER`, `BOT_DIR` and `VPS_SSH_KEY` exactly as the script printed them
   (Name in the small box, value in the big box). `MOMENTUM_TELEGRAM_TOKEN` should already be there.
2. GitHub → **Actions** → latest **Test and deploy to VPS** run → **Re-run all jobs**.

## 4. Telegram
- Send `/start` to your **main** bot (if you skipped the chat id).
- Send `/start` to your **momentum monitor** bot. Try `/status` and `/filter`.

## Choose the strategy
The new server starts with an empty account list, so it trades the MT5 account you entered with the
**momentum** strategy at 5% risk per trade. To use more accounts or change risk, use `/connect`, `/strategy`
and `/risk` in the main bot as before.

## If something goes wrong
| Problem | Fix |
|---|---|
| "bridge isn't connected" | Wrong MT5 login/password/server. `nano ~/fxalexg_bot/.env`, fix, then `sudo systemctl restart mt5-bridge fxalexg-bot` |
| Bot silent in Telegram | Send `/start` to the main bot; check `sudo journalctl -u fxalexg-bot -n 50` |
| Want to see it live | `sudo journalctl -u fxalexg-bot -f` (Ctrl+C to stop watching) |
| Server restarted | Nothing to do: all three services start by themselves |
