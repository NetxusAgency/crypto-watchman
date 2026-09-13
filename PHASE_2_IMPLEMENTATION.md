# TradeSentinel — Phase 2 Implementation Plan

## 1. Current Architecture

- **Language/Framework**: Python 3.11 / FastAPI (`app/main.py`), single `uvicorn` process on Render (no Celery in prod).
- **Telegram**: aiogram 3.x (`app/bot/`). `DbSessionMiddleware` injects an async SQLAlchemy session into handlers. Dispatcher, keyboards (reply + inline), FSM states in `app/bot/`.
- **Database**: SQLAlchemy 2.0 async + asyncpg (Neon PostgreSQL on Render; aiosqlite locally). Tables auto-created at startup via `Base.metadata.create_all` (no Alembic). Models in `app/database/models/__init__.py`: `User`, `Portfolio`, `Alert`, `Notification`, `Subscription`.
- **Background work**: APScheduler (`AsyncIOScheduler`) started in the FastAPI lifespan — alerts every 30s, listing monitor every 2m, daily digest at 09:00. Docker/Celery path exists but is not used on Render.
- **Existing integrations**:
  - Prices: CoinGecko (+ Binance/Coinbase/DexScreener fallbacks) for crypto; TwelveData → `open.er-api.com` fallback for forex. `app/services/prices/price_fetcher.py`.
  - Alerts: 7 alert types (`price_above`, `price_below`, `move_percent`, `volume_above`, `volatility`, `sentiment_spike`, `whale_alert`) in `app/services/alerts/alert_manager.py`. Alerts auto-deactivate after firing.
  - Listings: Binance/Bybit/Coinbase new-listing monitor with Redis dedup → pro/premium only.
  - Sentiment: Reddit r/cryptocurrency + CoinTelegraph/CoinDesk RSS **mention counts only** (`app/services/sentiment/`).
  - Whales: BTC ≥10 (mempool.space) + ETH ≥100 (Etherscan) only (`app/services/whale_tracker/`).
  - AI: `app/services/market_digest/digest_service.py` — `call_llm()` providers: **Groq (primary, `openai/gpt-oss-20b`)** → OpenAI → OpenRouter. Returns plain text; falls back to plain-text stats.
- **Limits**: free = 5 assets; `ADMIN_TELEGRAM_ID` auto-pro; listing alerts pro/premium only.
- **Tests**: none exist. `TESTING.md` documents manual flows only.

## 2. Existing Features

Portfolio CRUD, price above/below/move/volume/volatility/sentiment/whale alerts, analytics, sentiment mention counts, whale feed, AI digest, exchange-listing alerts, reply/inline menu navigation, free/pro tiering, daily digest, health endpoint (`/`, `/health`).

## 3. Proposed Modifications (summary)

Replace/upgrade three systems and add two new ones, preserving all current behavior:

1. **News sentiment** `app/services/sentiment/` → **AI News & Market Sentiment Engine** (`app/services/news/`). Counts become collected articles → deduped → AI-classified (bullish/bearish/neutral, impact, direction, event category) → digestible per-asset summaries.
2. **Whale tracker** → multi-asset whale monitoring over an `assets` registry with provider abstraction (Phase 2B).
3. **Digest** → grows into Trading Assistant (Phase 2C) with strategy engine + indicators.
4. **New**: Web3 wallet portfolio (Phase 2D) + Telegram Mini App (Phase 2E).
5. **New**: Opportunity detection engine (Phase 2F) combining price/volume/news/social/listings/whales.

Core architectural principle from the plan: *APIs/feeds collect facts → DB stores temporarily → algorithms compute measurable signals → AI interprets → Telegram delivers.* The AI never invents the strategy, the events, or the score — it interprets evidence.

## 4. New Dependencies

Minimal changes to `requirements.txt` (everything else is stdlib + existing SQLAlchemy/aiogram/httpx):

- `pytest` + `pytest-asyncio` — new test harness (dev-only).

RSS/Atom parsing uses Python's stdlib `xml.etree.ElementTree` (mirrors the existing sentiment-monitor style), so no feed parser dependency is needed.

## 5. New Database Tables

All additive — existing tables untouched. Declared under `app/database/models/` and auto-created like current models.

| Table | Purpose |
|-------|---------|
| `news_articles` | Temp raw news store (source, url, title, desc, content, published_at, fetched_at, hash, related_assets, processing_status, expires_at, per-article AI sentiment when classified). Retention-controlled. |
| `news_analysis` | Per-asset AI verdicts: overall sentiment, impact level/direction, confidence, reason, time horizon, event categories, positive/neutral/negative counts, analyzed_at. |
| `assets` | Dynamic asset registry: symbol, name, chain, contract, decimals, market_cap, liquidity, reputation, tiers, flags (Phase 2B). |
| `whale_transactions` | Stored whale txs for multi-asset monitoring (Phase 2B). |
| `trading_strategies` / `strategy_versions` | Normalized user strategies + versioning (Phase 2C). |
| `trade_analyses` | Cached AI trade analysis outputs (Phase 2C). |
| `wallets` / `wallet_assets` | Connected public wallets + balances (Phase 2D). |
| `opportunity_signals` / `opportunity_scores` | Catalyst evidence + computed scores (Phase 2F). |
| `notification_preferences` | Per-user alert thresholds/priorities (LOW/MEDIUM/HIGH/CRITICAL). |

## 6. API Integrations Required

- **Google News RSS** — `https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US` (free, ToS-safe, no key). Primary source.
- **Crypto news RSS/Atom** — CoinTelegraph, CoinDesk, Decrypt, The Block, CryptoSlate, Bitcoin Magazine, NewsBTC, BeInCrypto (free feeds).
- **Exchange announcements** — Binance public CMS API (`article/list/query?type=1&pageNo=1&pageSize=10`) and Coinbase blog RSS. Bybit's public v5 announcements endpoint currently rejects keyless requests — parsed via graceful degradation only (skipped). All parsers fail soft.
- **Reddit** — existing public JSON endpoint, kept (no key).
- **X/Twitter** — optional, **requires** official API keys; skipped in Phase 2A (flagged TODO).
- **Whales (2B)** — Etherscan (existing) + optional Moralis/Alchemy/Helius public plans.
- **Wallets (2D)** — Moralis/Alchemy/Covalent for ERC-20 balance reads.
- **AI** — existing `call_llm` (Groq primary, now takes a `max_tokens` argument). No new dependency needed for 2A.

## 7. Environment Variables

Added (all optional except existing ones; defaults safe):

```
NEWS_RETENTION_HOURS=48        # raw news retention window (24-72 recommended)
NEWS_REFRESH_MINUTES=10        # background collect+analyze interval
NEWS_MAX_QUERIES_PER_ASSET=3   # Google News query depth per asset
NEWS_ANALYSIS_INTERVAL_GROUPS=1 # (reserved)
```

Existing vars kept as-is (`TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, `ADMIN_TELEGRAM_ID`, `GROQ_API_KEY`/`GROQ_MODEL`, `ETHERSCAN_API_KEY`, etc.). Nothing secret is ever committed.

## 8. Migration Plan

No destructive migration. Because tables are created by `Base.metadata.create_all`, new tables appear automatically on deploy. For the **IBefore-a deployment with existing `crypto_watchman.db` (aiosqlite) or Neon Postgres: `create_all` only creates missing tables — safe. Add a small `sqlite`/Postgres-compatible check at startup. No Alembic introduced yet (matches current repo state).

## 9. Testing Plan

- Add `pytest` + `pytest-asyncio`; new `tests/` tree mirroring the plan.
- Tests: news source parsing (fixture RSS/Atom strings), normalization, dedup hash collision behavior, retention cleanup, AI prompt/JSON parsing (mocked LLM), `/news` handler (mocked session), scheduler task error isolation.
- Keep all existing manual flows per `TESTING.md`.

## 10. Implementation Order

Phase 2A (this delivery) → 2B whales → 2C trading assistant → 2D wallets → 2E Mini App → 2F opportunity engine.

### Phase 2A — AI News Intelligence ✅ done
1. News DB models + `NEWS_RETENTION_HOURS` config.
2. Source abstraction (`NewsSource` base + normalized article) + Google News RSS.
3. RSS + exchange sources (CoinGecko-adjacent crypto news feeds + Binance/Coinbase announcements).
4. Normalization + dedup (hash on title/url/content) + storage (`news_articles`).
5. AI analysis via `call_llm`: per-asset sentiment/impact/direction/reason/confidence + event categories → `news_analysis`.
6. `/news` Telegram command + 📰 News menu button.
7. Background ingestion job + retention cleanup job in APScheduler.
8. Verify: compile all modules; manual test via `/news`.

### Phase 2B — Multi-Asset Whales ✅ done
Asset registry + `whale_transactions` store, `WhaleProvider` abstraction (BTC mempool→Blockstream fallback, ETH native + ERC-20 via Etherscan), registry-driven thresholds/tiering, DB-backed dedup, `/whale` + menu multi-asset display, 5-min background scan job.

### Phase 2C — Trading Assistant
Coin/timeframe selection flow, OHLCV + indicators (MA/RSI/MACD/ATR), strategy storage/parsing, AI analysis → entry/SL/TP/R:R output. Analysis only — no auto-execution.

### Phase 2D — Web3 Portfolio
Public-address-only wallet connect (never seed/private keys), balance aggregation, provider abstraction, Telegram display.

### Phase 2E — Mini App
Telegram Mini App front end over the existing API + new `/api` routes.

### Phase 2F — Opportunity Engine
Deterministic weighted score (news 20 / volume 20 / momentum 15 / social 15 / listing 15 / whale 10 / liquidity 5) + AI interpretation, aligned with alert priorities.