# Flower Bot

Minimal Telegram bot for a flower shop built with aiogram. The bot shows available bouquets and lets users place orders.

## Prerequisites

- Python 3.10+
- Telegram bot token from BotFather
- Optional payment provider token for processing payments
- Optional comma-separated Telegram user IDs for administrators

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your tokens and admin IDs
python3 flower_bot.py
```

## Environment variables

Create a `.env` file with:

```
BOT_TOKEN=your_bot_token_here
PROVIDER_TOKEN=your_payment_provider_token_or_leave_blank
ADMIN_IDS=123456789  # Comma-separated admin Telegram user IDs
```
