# ☁️ Deploying to Cloud PaaS (Without Docker)

This guide walks you through deploying **Crypto & Forex Watchman** to cloud PaaS platforms like **Railway**, **Render**, **Fly.io**, or **Heroku** using native Python buildpacks. You do not need to install Docker locally or run Docker containers.

---

## ⚡ Option A: Deployment on Railway (Recommended)

Railway automatically detects Python applications via Nixpacks, provisions PostgreSQL and Redis databases with 1-click, and handles environment variables seamlessly.

### Steps:
1. Go to [Railway.app](https://railway.app) and sign in with GitHub.
2. Click **New Project** $\rightarrow$ **Deploy from GitHub repo**.
3. Select your `CryptoAuto` repository.
4. Add Database Services:
   - Click **+ New** $\rightarrow$ **Database** $\rightarrow$ **Add PostgreSQL**.
   - Click **+ New** $\rightarrow$ **Database** $\rightarrow$ **Add Redis**.
5. Set Environment Variables in your Web Service:
   - `TELEGRAM_BOT_TOKEN`: `your_bot_token_from_botfather`
   - `DATABASE_URL`: `${{ Postgres.DATABASE_URL }}` (Railway auto-references it)
   - `REDIS_URL`: `${{ Redis.REDIS_URL }}` (Railway auto-references it)
   - `OPENAI_API_KEY`: `sk-...` *(optional, for AI market digest)*
   - `ETHERSCAN_API_KEY`: `your_etherscan_key` *(optional, for ETH whale tracking)*
   - `ADMIN_TELEGRAM_ID`: `123456789` *(optional, grants admin pro tier)*
6. Railway will build and deploy your app. The `/health` route will confirm the app is live!

---

## 🚀 Option B: Deployment on Render

Render supports 1-click infrastructure deployment using the included [`render.yaml`](file:///C:/Users/freedextech/Desktop/CryptoAuto/render.yaml) file.

### Steps:
1. Push your code to GitHub.
2. Log into [Render.com](https://render.com).
3. Click **New +** $\rightarrow$ **Blueprints**.
4. Connect your GitHub repository.
5. Render will automatically detect [`render.yaml`](file:///C:/Users/freedextech/Desktop/CryptoAuto/render.yaml) and create:
   - A Web Service (`crypto-watchman`)
   - A Managed PostgreSQL Database (`crypto-watchman-db`)
   - A Managed Redis Instance (`crypto-watchman-redis`)
6. Enter your secret environment variables (`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, etc.) when prompted.
7. Click **Apply**. Render will deploy everything!

---

## 🦅 Option C: Deployment on Fly.io

1. Install the Fly CLI (`flyctl`).
2. Run `fly launch` in the repository root directory.
3. Attach PostgreSQL: `fly postgres create` and `fly postgres attach`.
4. Attach Redis: `fly redis create`.
5. Set secrets:
   ```bash
   fly secrets set TELEGRAM_BOT_TOKEN="your_token" OPENAI_API_KEY="sk-..."
   ```
6. Run `fly deploy`.

---

## 🔍 How Cloud PaaS Execution Works

When deployed on a cloud PaaS:
1. **Single Entrypoint**: The server runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT` specified in the [`Procfile`](file:///C:/Users/freedextech/Desktop/CryptoAuto/Procfile).
2. **Lifespan Task Execution**:
   - `app/main.py` automatically initializes database tables on startup.
   - The internal `AsyncIOScheduler` runs alert checks (every 30s), new exchange listing monitoring (every 2m), and daily market digests (at 09:00 UTC).
   - Aiogram runs polling to respond to user Telegram commands in real-time.
3. **PaaS Health Checks**: The platform ping `/health` to verify service uptime.
