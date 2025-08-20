# Flower_bot
## Запуск проекта
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Заполни .env своими данными (BOT_TOKEN, PROVIDER_TOKEN, ADMIN_IDS)
python3 flower_bot.py
```

## Database

The SQLite database file (`flower_shop.db` and its `-wal`/`-shm` companions) is ignored by Git.
If you have an old copy checked out locally, remove those files before running the bot.
The application will automatically create a fresh database at startup if one is missing.
