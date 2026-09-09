# Crypto & Forex Watchman — AGENTS.md

## Entrypoint

- FastAPI app: `app/main.py` — lifespan handler auto-creates DB tables, starts APScheduler (30s alerts, 2min listings), starts aiogram polling.
- Docker Compose: `docker compose up --build` runs Postgres, Redis, FastAPI, Celery worker + Celery beat.

## Commands

| Action | Command |
|--------|---------|
| Local dev | `uvicorn app.main:app --reload` |
| Full stack | `docker compose up --build` |
| Celery worker | `celery -A app.workers.celery_app worker --loglevel=info` |
| Celery beat | `celery -A app.workers.celery_app beat --loglevel=info` |

No tests, lint, typecheck, or pre-commit config exists. `pip install -r requirements.txt` to install.

## Architecture

- **aiogram 3.x** for Telegram bot; `DbSessionMiddleware` injects async SQLAlchemy session into every handler.
- **Two parallel background schedulers** (APScheduler when local, Celery beat when Docker). Both call the same `check_all_alerts()` and `listing_monitor.check_listings()`. Never run both simultaneously — they will duplicate work.
- **Database**: SQLAlchemy 2.0 async with `asyncpg`. Auto-creates tables on startup (`Base.metadata.create_all`). No Alembic migrations configured despite `alembic` in requirements.
- **Session engine**: `AsyncEngineProxy` / `AsyncSessionMakerProxy` recreate the engine when PID changes (Celery fork safety).
- **Prices**: CoinGecko for crypto (hardcoded mapping in `CRYPTO_MAPPING`, dynamic search fallback). TwelveData for forex, falling back to `open.er-api.com`. No API keys required for basic operation.
- **Listing monitor**: polls Binance, Bybit, Coinbase APIs; deduplicates via Redis SETs (key: `seen_listings:{exchange}`). First run populates cache silently.
- **Market digest**: generates AI-powered portfolio summary via OpenAI (gpt-4o-mini). Falls back to plain-text stats if no API key set. Called on-demand via `/digest`; also auto-sent daily at 09:00 to Pro/Premium users.
- **Sentiment monitor**: scans Reddit r/cryptocurrency + CoinTelegraph/CoinDesk RSS for portfolio asset mentions (24h window). Called on-demand via `/sentiment`.
- **Whale tracker**: fetches large BTC txs (≥10 BTC) from mempool.space and large ETH txs (≥100 ETH) from Etherscan (requires `ETHERSCAN_API_KEY`). Called on-demand via `/whale`; also powers `whale_alert` auto-notifications via the background alert checker.
- **Tier limits**: `free` = max 5 assets. Listing alerts only sent to `pro`/`premium` users. No payment integration yet.

## Quirks

- `.env` loaded by `pydantic-settings` automatically; `python-dotenv` is unused.
- `ADMIN_TELEGRAM_ID` in `.env` auto-grants `pro` tier to that user (bypasses 5-asset cap, gets listing alerts).
- `TELEGRAM_BOT_TOKEN` default is `"placeholder_token"` — bot polling skips if token is the placeholder.
- Alerts auto-deactivate after firing (`is_active = False`) to prevent spam.
- Alert types stored as `price_above` / `price_below` / `move_percent` / `volume_above` / `volatility` / `sentiment_spike` / `whale_alert` strings in DB.
- All handler commands require asset to be in portfolio before setting an alert.
- Forex detection heuristic: if symbol length = 6 with fiat codes, treat as forex.
