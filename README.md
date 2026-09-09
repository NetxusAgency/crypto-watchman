# Crypto & Forex Watchman Telegram Bot

An automated 24/7 portfolio monitoring and opportunity detection Telegram bot built using **FastAPI**, **aiogram**, **SQLAlchemy (PostgreSQL)**, **Redis**, and **Celery**.

This bot helps users track their crypto and forex asset portfolios, setting price threshold triggers (`above` and `below`) that send push alerts directly inside Telegram.

---

## 📁 Project Structure

```text
CryptoAuto/
├── app/
│   ├── bot/                 # Telegram Bot logic (aiogram)
│   │   ├── handlers/        # Command and event handlers
│   │   ├── middlewares/     # Bot middleware (e.g., db session injector)
│   │   └── dispatcher.py    # Bot and dispatcher setup
│   ├── core/                # Core settings and logger
│   ├── database/            # DB configuration & SQLAlchemy models
│   ├── services/            # Prices fetcher & alert manager
│   ├── workers/             # Celery worker and periodic tasks
│   └── main.py              # Application entry point (FastAPI)
├── Dockerfile               # Production Dockerfile
├── docker-compose.yml       # Docker compose setup
├── requirements.txt         # Python dependencies
├── plan.md                  # Development blueprint
└── README.md                # Documentation
```

---

## ⚙️ Requirements & Installation

### Option 1: Running Locally (For Development)

1. **Clone the Repository** and navigate to the project directory.
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Configure Environment**:
   Copy `.env.example` to a new file named `.env` and fill in your settings:
   ```bash
   cp .env.example .env
   ```
   * *Required*: `TELEGRAM_BOT_TOKEN` (Create one via [@BotFather](https://t.me/BotFather))
   * *Optional*: `COINGECKO_API_KEY`, `TWELVEDATA_API_KEY` (Not strictly required; fallback public/free endpoints are built-in)
4. **Run Postgres & Redis**:
   Make sure you have PostgreSQL and Redis servers running locally and update the database/redis URLs in `.env`.
5. **Start the Application**:
   ```bash
   uvicorn app.main:app --reload
   ```
   * *Note*: When running locally with `app.main:app`, an **internal background scheduler** (APScheduler) automatically runs every 30 seconds to fetch prices and check alerts. You do not need to run Celery separately for local testing!

### Option 2: Running with Docker Compose

1. **Configure Environment**: Make sure `.env` contains your `TELEGRAM_BOT_TOKEN`.
2. **Launch Services**:
   ```bash
   docker compose up --build
   ```
   This command starts the following services:
   * **`db`**: PostgreSQL instance mapping to port `5432`
   * **`db_redis`**: Redis instance mapping to port `6379`
   * **`app`**: FastAPI server & Telegram bot long polling listener (mapping to port `8000`)
   * **`celery_worker`**: Celery worker executing alert checks
   * **`celery_beat`**: Celery beat scheduler triggering price checks every 30 seconds

---

## 🤖 Bot Commands

Once your bot is running, message it in Telegram with the following commands:

* **/start** - Register user profile and display welcome instructions.
* **/help** - Detailed formatting and usage guide.
* **/portfolio** - View current assets in your portfolio.
* **/add_asset `<symbol>` `<entry_price>`** - Add or update a coin or forex pair.
  * *Example*: `/add_asset BTC 65000`
  * *Example*: `/add_asset EURUSD 1.0825`
* **/remove_asset `<symbol>`** - Remove an asset from your portfolio.
  * *Example*: `/remove_asset BTC`
* **/alerts** - View active price thresholds.
* **/add_alert `<symbol>` `<above/below>` `<target_price>`** - Set a trigger alert.
  * *Example*: `/add_alert BTC above 70000`
  * *Example*: `/add_alert EURUSD below 1.07`
* **/remove_alert `<alert_id>`** - Cancel a active alert by its ID.
  * *Example*: `/remove_alert 3`
* **/settings** - Check subscription tier (Free vs Pro) and active alerts.
