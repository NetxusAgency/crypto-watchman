# Crypto & Forex Watchman Bot: Implementation Checklist & Progress

Here is the implementation checklist outlining completed items and the remaining roadmap based on [plan.md](file:///C:/Users/freedextech/Desktop/CryptoAuto/plan.md).

---

## 📌 Phase 1: MVP (Current Status)

### 1. User Management & Telegram Interface
- [x] **`/start`** command: Registers the user in the database and shows welcome guidelines.
- [x] **`/help`** command: Lists bot commands and details parameters.
- [x] **`/settings`** command: Displays account details, subscription tier, and asset counts.
- [x] **Registration Database Integration**: Auto-registers users when they interact with the bot.
- [x] **Admin Auto-Pro**: `ADMIN_TELEGRAM_ID` in `.env` auto-grants `pro` tier to that user (bypasses 5-asset cap, gets listing alerts).

### 2. Portfolio Management
- [x] **`/portfolio`** command: Lists all tracked symbols with their user entry prices.
- [x] **`/add_asset <symbol> <entry_price>`**: Adds/updates crypto and forex pairs with validation.
- [x] **`/remove_asset <symbol>`**: Deletes assets and associated alerts from the user's watchlist.
- [x] **Free Tier Constraints**: Enforces a strict maximum limit of **5 assets** for users on the `free` tier.

### 3. Smart Alert Engine (Price Triggers)
- [x] **`/alerts`** command: Lists active threshold triggers set by the user.
- [x] **`/add_alert <symbol> <above/below> <price>`**: Sets boundary triggers.
- [x] **`/remove_alert <alert_id>`**: Cancels alert rules by their unique database ID.
- [x] **Trigger Alert Delivery**: Bot sends trigger notifications containing asset ticker, target vs actual price, and calculated entry-to-trigger PnL %.
- [x] **Auto-Deactivation**: Deactivates alerts after triggering to prevent spamming.
- [x] **Percentage Move Alerts**: Triggers when asset moves by ±X% from entry price (alert type `move_percent`).
- [x] **Volume Alerts**: Alerts when 24h volume exceeds an absolute threshold (alert type `volume_above`).
- [x] **Volatility Alerts**: Alerts when daily price move exceeds Nx normal volatility (z-score, alert type `volatility`).

### 4. Price Monitoring Engine
- [x] **Multi-Source Fetcher**: Custom client parsing tickers to retrieve spot prices:
  - Uses **CoinGecko** for crypto assets.
  - Uses **TwelveData** for forex pairs.
  - Uses a keyless fallback API (**`open.er-api.com`**) to fetch USD forex pairs dynamically without requiring API keys.
- [x] **Asynchronous DB Processing**: Checks active alerts dynamically in background tasks.

### 5. Exchange Listing Monitor
- [x] **Listing APIs Checker**: Service wrapper polling spot listings from **Binance**, **Coinbase**, and **Bybit**.
- [x] **Caching/Filtering Layer**: Implements a Redis Set comparison to ensure only newly listed tokens trigger events (ignores existing listing database history).
- [x] **Integration into Celery Workers**: Run listing checks periodically.
- [x] **Integration into APScheduler**: Run listing checks locally.
- [x] **Pro/Premium Tier Access Restriction**: Ensures only users with `pro` or `premium` plans receive listing alerts.


### 6. Infrastructure & Operations
- [x] **Docker Stack Setup**: Configuration for Postgres database, Redis cache, FastAPI app, Celery worker, and Celery beat.
- [x] **Multiprocessing Database Fix**: Process-aware dynamic engine proxy to resolve connection pool sharing issues in Celery worker processes.
- [x] **FastAPI Lifespan Web Interface**: Auto-migrates database schemas, hosts API check endpoints, and starts internal background loops.
- [x] **FastAPI Health Route**: Endpoint (`/health`) validating server and bot connectivity.
- [x] **Internal Scheduler Fallback**: Integrates `APScheduler` into FastAPI to allow running the full application locally *without* Docker or Postgres (SQLite via aiosqlite).

---

## 🚀 Phase 2: Advanced Features (Planned)

### 1. Monetization & Subscriptions
- [ ] Integrate Stripe or Crypto payment gateway.
- [ ] Automatically update user database plan on successful transaction.
- [ ] Cron task to scan and expire subscriptions past their expiry date.

### 2. Social Sentiment Monitoring
- [x] Poll mentions of watchlisted assets on Reddit (r/cryptocurrency) and crypto news feeds (CoinTelegraph, CoinDesk RSS).
- [x] **`/sentiment`** command: Shows Reddit + news mention counts per asset.
- [x] Alert users on high mention increases (`/add_alert BTC sentiment 10` — alert when 24h mentions > 10).
### 3. Whale Transaction Tracker

- [x] `/whale` command: Shows recent large BTC/ETH blockchain transactions (scans latest block, not hardcoded address).
- [x] Generate auto-alerts for large transaction movements (`/add_alert BTC whale 10`).

### 4. Advanced Analytics & AI
- [x] **`/analytics`** command: Shows per-asset PnL, best/worst performers, and price breakdown.
- [x] **`/digest` command**: Generates AI-powered market summary (OpenAI gpt-4o-mini, falls back to plain-text).
- [x] **Daily auto-digest**: Sent at 09:00 to Pro/Premium users via APScheduler cron job.

---

## Known Issues (Fixed)

- ✅ **Dead import**: Removed unused `re` from `sentiment_monitor.py`.
- ✅ **ETH whale hardcoded address**: Rewrote to scan latest block via Etherscan proxy API (`eth_getBlockByNumber`) — catches all large ETH transfers, not just one wallet's.
- ✅ **CoinDesk/CoinTelegraph RSS blocking**: Added browser-like headers (`User-Agent`, `Accept`, `Accept-Language`) to RSS requests.
- ✅ **Redis crash in listing monitor**: Wrapped all Redis operations (`exists`, `smembers`, `sadd`) in try/except so the listing check degrades gracefully if Redis is down.
- ✅ **Model import fragility**: Flattened `app/database/models` — model classes now live directly in `__init__.py` instead of nested `models/models.py`, removing the redundant indirection.
