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
git clone https://github.com/stryyxTG/afkb.git
cd afkb
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
TRIGGER_WORD=тг
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
sudo -u tgbot git clone https://github.com/stryyxTG/afkb.git /opt/tgbot
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