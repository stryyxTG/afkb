# TGbot

Telegram bot for storing Telegram sessions, issuing account phone numbers by trigger, and forwarding incoming Telegram login codes from the account itself.

## Requirements

- Python 3.10+
- Git
- systemd-based Linux server
- Telegram bot token
- Telegram API ID/hash from https://my.telegram.org/apps

## Local/Manual Start

```bash
git clone https://github.com/stryyxTG/TGbot.git
cd TGbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python bot1.py
```

Required `.env` values:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
TELEGRAM_API_ID=2040
TELEGRAM_API_HASH=...
TELEGRAM_PROXY=
TRIGGER_CHAT_ID=-1001234567890
```

`storage/` is created locally and contains the SQLite database, uploaded sessions and JSON files. Do not commit it.

## Server Install With systemd

Example path: `/opt/tgbot`.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
sudo useradd --system --create-home --shell /usr/sbin/nologin tgbot || true
sudo mkdir -p /opt/tgbot
sudo chown -R tgbot:tgbot /opt/tgbot
sudo -u tgbot git clone https://github.com/stryyxTG/TGbot.git /opt/tgbot
cd /opt/tgbot
sudo -u tgbot python3 -m venv .venv
sudo -u tgbot .venv/bin/pip install -r requirements.txt
sudo -u tgbot cp .env.example .env
sudo -u tgbot nano .env
```

Install service:

```bash
sudo cp /opt/tgbot/deploy/tgbot.service.example /etc/systemd/system/tgbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now tgbot
```

Check logs:

```bash
sudo systemctl status tgbot
sudo journalctl -u tgbot -f
```

Restart after updates:

```bash
cd /opt/tgbot
sudo -u tgbot git pull
sudo -u tgbot .venv/bin/pip install -r requirements.txt
sudo systemctl restart tgbot
```

## Notes

- `.env`, `storage/`, sessions, JSON files and the SQLite DB must stay only on the server.
- Proxy can be set from the bot menu or by `TELEGRAM_PROXY` in `.env`.
- For imported accounts, `.session` and `.json` must have matching base names.
## Workers and statistics

Admins can open **Воркеры** from the main menu or use `/workers` to add a worker by numeric Telegram ID, view their username, and revoke access. If Telegram cannot resolve the username immediately, it is updated from the worker's next accepted trigger/button interaction. Workers without a username are shown by name and ID.

Use /admins to view the administrator list, grant access by Telegram ID, or revoke another administrator''s access. Changes take effect immediately and the managed ID list is saved in ADMIN_IDS in .env; the last administrator cannot be removed.

Only listed workers and bot admins may request accounts in `TRIGGER_CHAT_ID`. Workers do not have access to the admin menu. There are no per-worker quotas; each trigger requests at most 10 accounts.

`/stats` shows the number of requests, requested accounts, and accounts whose code was successfully sent. Empty-stock requests count too. Only workers in the list are included in these statistics; admins can request accounts without being added. Repeat-code buttons do not count as a new account request or issuance.

Statistics can be reset for one worker or everyone (also via `/reset`), with confirmation. Reset preserves access and accounts; pending requests from before the reset do not repopulate the cleared statistics. Revoking a worker removes their statistics. Data is stored in SQLite and survives restarts.

In a batch, each account moves to `issued` only after its code is delivered. Accounts still awaiting a code stay reserved; those without a delivered code return to their original stage when the watcher finishes (normally after 10 minutes).
