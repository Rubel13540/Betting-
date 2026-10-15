# Telegram Website Health-Check Bot

A small, bounded website smoke-test bot for domains you own or are authorized to test.
It is **not** a fake-view, ad-click, or human-traffic generator.

## Features
- Bangla Telegram UI
- Home, Back, Stop
- Select 1 / 5 / 10 / 20 / 50 checks
- Select 10 / 15 / 30 / 60 second intervals
- Progress message every 20 successful checks
- Stops on first HTTP 4xx/5xx or network/timeout error and reports a reason
- Exact domain allowlist

## Railway setup
Create a Railway project from this folder/repository and set these Variables:

- `BOT_TOKEN`: Telegram bot token from BotFather
- `ADMIN_ID`: your numeric Telegram user ID
- `TEST_ALLOWED_HOSTS`: comma-separated exact hostnames you own, e.g. `example.com,test.example.com`

Do not include `https://` in `TEST_ALLOWED_HOSTS`. The URL submitted to the bot must use HTTPS and its hostname must exactly match an allowed host.

Railway Start Command can be `python main.py`. The included `Procfile` uses a worker process.

## Safety limits
The bot caps checks at 50 and requires at least 10 seconds between checks. It performs sequential HTTP health checks, not human-like browser visits. Do not use it for third-party sites without permission, ad clicks, or fake engagement.

## Local run
```bash
python -m venv .venv
# activate the environment, then:
pip install -r requirements.txt
export BOT_TOKEN="..."
export ADMIN_ID="123456789"
export TEST_ALLOWED_HOSTS="example.com"
python main.py
```
