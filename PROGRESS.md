# TradeSentinel — Phase 2 Progress Log

Work-in-progress log for the Phase 2 expansion of **Crypto & Forex Watchman → TradeSentinel**.
Full design lives in `PHASE_2_IMPLEMENTATION.md`.

## Status Summary

| Phase | Feature | Status |
|-------|---------|--------|
| 2A | AI News + Market Sentiment Engine | **Implemented & tested** |
| 2B | Multi-Asset Whale Monitoring | **Implemented & tested** (this delivery) |
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

## 2B — Multi-Asset Whale Monitoring ✅

Replaced the fixed BTC(+optional ETH) tracker with a provider-abstraction whale monitor covering a DB-backed asset registry.

### New files
- `app/database/models/whale.py` — `Asset` (registry: symbol, name, chain, contract, decimals, `whale_threshold`, tier, flags) + `WhaleTransaction` (deduped by `(asset, txid)` SHA-256)
- `app/services/whale_tracker/assets.py` — `DEFAULT_ASSETS` (BTC, ETH, USDT, USDC, LINK, UNI, MATIC, SHIB, PEPE) + `seed_assets()` upsert
- `app/services/whale_tracker/providers.py` — `WhaleProvider` ABC + `WhaleTransfer` dataclass; `BitcoinWhaleProvider` (mempool.space → Blockstream fallback), `EthereumWhaleProvider` (native ETH, Etherscan proxy), `Erc20WhaleProvider` (per-token `tokentx`, auto decimals conversion)

### Modified files
- `app/services/whale_tracker/whale_tracker.py` — rewritten: multi-provider aggregation, registry-threshold filtering, `fetch_and_store()` DB-backed dedup + USD value, `get_whales(session)` for the menu
- `app/services/alerts/alert_manager.py` — whale alerts now use `fetch_and_store` (persistent dedup across restarts); alert message shows ≈ USD
- `app/bot/handlers/menu.py` + `app/bot/handlers/whale.py` — pass `session`; multi-asset display with per-asset symbols, USD value, updated legend
- `app/main.py` — seeds asset registry on startup; new background `run_whale_scan` job (every `WHALE_SCAN_MINUTES`)
- `app/core/config.py` — `WHALE_SCAN_MINUTES`, `WHALE_MAX_ITEMS_PER_ASSET`
- `app/database/models/__init__.py` — import new models

### Tests
- New `tests/test_whale.py` — 7 more tests (30 total, all passing):
  dedup-key correctness, DEFAULT_ASSETS validity, BTC provider sats→BTC parsing (MockTransport), ERC-20 decimals conversion, `fetch_and_store` dedup + USD attribution, sub-threshold filtering.

### Deploy/runtime notes
- `whale_transactions` auto-created on startup. `seed_assets()` is idempotent.
- BTC parity: mempool.space primary, Blockstream Esplora fallback (some regions block mempool.space — that's the case on this dev machine; Render US should reach both).
- ETH + ERC-20 scanning requires `ETHERSCAN_API_KEY`; skips silently without it.
- Whale scan job runs every 5 min to keep `whale_transactions` populated even without whale alerts; alert checks default back to it every 30s.

---

## Next up: Phase 2C — AI Trading Assistant