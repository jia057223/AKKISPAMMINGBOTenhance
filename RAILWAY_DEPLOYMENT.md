# Railway Deployment

This project is configured for Railway as a worker bot.

## Required Railway Variables

Set these in Railway -> Variables:

```env
BOT_TOKEN=your_bot_token
API_ID=12345678
API_HASH=your_api_hash
ADMIN_IDS=123456789
DATABASE_URL=postgresql://...
LOG_JSON=1
```

`DATABASE_URL` is required in Railway/production. Attach a Railway PostgreSQL
service so Railway injects it automatically.

## Start Command

Railway uses:

```bash
cd bot && python main.py
```

This is configured in:

- `railway.toml`
- `nixpacks.toml`
- `Procfile`

## Install Command

Nixpacks installs Python dependencies from:

```bash
pip install --no-cache-dir -r bot/requirements.txt
```

## Health Check

For a manual deployment check:

```bash
cd bot && python healthcheck.py
```

The health check verifies required environment variables and PostgreSQL
connectivity.

## Migration Behavior

The owner-isolation migration is clean-start by design:

- old mixed `accounts` are dropped once
- old mixed `messages` are dropped once
- owner-scoped tables are created
- future starts do not repeat the destructive migration

The migration marker is stored in `schema_migrations`.
