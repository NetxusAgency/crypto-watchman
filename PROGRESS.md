# TradeSentinel — Phase 2 Progress Log

Work-in-progress log for the Phase 2 expansion of **Crypto & Forex Watchman → TradeSentinel**.
Full design lives in `PHASE_2_IMPLEMENTATION.md`.

## Status Summary

| Phase | Feature | Status |
|-------|---------|--------|
| 2A | AI News + Market Sentiment Engine | **Implemented & tested** (this commit) |
| 2B | Multi-Asset Whale Monitoring | Not started |
| 2C | AI Trading Assistant | Not started |
| 2D | Web3 Portfolio | Not started |
| 2E | Telegram Mini App | Not started |
| 2F | Opportunity Engine | Not started |

---

## 2A — AI News Intelligence ✅

Core principle applied: **APIs/feeds collect facts → DB stores temporarily → AI interprets → Telegram delivers.**
The AI classifies evidence only; it never invents events or predicts guaranteed prices. Output uses
Bullish/Bearish/Neutral, impact levels, "potential direction", and confidence — no guaranteed claims.

### New files
- `app/database/models/news.py` — `NewsArticle` (temp store, `expires_at`, `hash` dedup, `processing_status`) and `NewsAnalysis` (per-asset AI verdict)
- `app/bot/handlers/news.py` — `/news` Telegram command
- `app/services/news/` — engine package:
  - `client.py` — shared httpx client (closed on shutdown in `main.py`)
  - `base.py` — `NormalizedArticle` dataclass + stdlib RSS/Atom parser (mirrors existing sentiment-monitor style, zero new deps)
  - `queries.py` — `ASSET_NAMES` map, full-name search queries (never bare tickers), forex exclusion
  - `sources.py` — Google News RSS (primary), 8 crypto news feeds, Binance CMS API + Coinbase blog RSS (fails soft, 8s timeout + TTL cache)
  - `collector.py` — fetch per asset, dedup by SHA-256 of URL/title
  - `analyzer.py` — one Groq call per asset → JSON verdict (per-article sentiment + aggregate)
  - `news_service.py` — orchestration (`refresh_asset_news`, `refresh_all`, `cleanup_expired`), Telegram formatting

### Modified files
- `app/database/models/__init__.py` — import new models (auto table creation)
- `app/core/config.py` — `NEWS_RETENTION_HOURS=48`, `NEWS_REFRESH_MINUTES=10`, `NEWS_MAX_QUERIES_PER_ASSET=3`
- `app/services/db_service.py` — `all_portfolio_symbols()` helper
- `app/services/market_digest/digest_service.py` — `call_llm(..., max_tokens)` param (digest stays compatible)
- `app/bot/keyboards.py` — added 📰 News button, layout `adjust(3, 3, 3)`
- `app/bot/dispatcher.py` — registered `news_handlers`
- `app/bot/handlers/menu.py` — menu button → news report; added to FSM-cancel set
- `app/main.py` — scheduler jobs: news refresh every `NEWS_REFRESH_MINUTES`, cleanup every 6h; close news client on shutdown
- `requirements.txt` — added `pytest`

### Tests
- New `tests/test_news.py` — 23 tests, all passing (`python -m pytest tests -q`):
  RSS/Atom parsing, date parsing, HTML stripping, hash determinism/distinctness, forex exclusion,
  query building, JSON extraction (code fences/prose), sentiment coercion, source token matching, formatting.
- New root `conftest.py` — makes the repo importable to pytest.

### Deploy notes
- New tables auto-created by `Base.metadata.create_all` on startup (no Alembic).
- New env vars are optional with safe defaults (no Render config change required).
- Keyless sources only; Bybit public announcements API rejects unauthenticated calls (skipped, fails soft).

### Verified live (2026-09-12)
- Google News + RSS + Binance parse real content (network-tested).
- Full pipeline: collect → store (dedup 0 on second run) → Groq analysis → formatted report.
- Sample output for BTC: 30 articles, 6/6/13 positive/negative/neutral, NEUTRAL, MEDIUM impact (55/100),
  NEUTRAL direction, 70% confidence.

---

## Next up: Phase 2B — Multi-Asset Whale Monitoring
Proposed scope in `PHASE_2_IMPLEMENTATION.md`: `assets` registry + `whale_transactions`,
chain/provider abstraction, multi-token whale detection (BTC/ETH first), liquidity/reputation tiering,
whale alerts beyond the current mempool.space + Etherscan pair.