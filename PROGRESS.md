# TradeSentinel — Phase 2 Progress Log

Work-in-progress log for the Phase 2 expansion of **Crypto & Forex Watchman → TradeSentinel**.
Full design lives in `PHASE_2_IMPLEMENTATION.md`.

## Status Summary

| Phase | Feature | Status |
|-------|---------|--------|
| 2A | AI News + Market Sentiment Engine | **Implemented & tested** |
| 2B | Multi-Asset Whale Monitoring | **Implemented & tested** |
| 2C | AI Trading Assistant | **Implemented & tested** |
| 2D | Web3 Portfolio | **Implemented & tested** |
| 2E | Telegram Mini App | **Implemented & tested** |
| 2F | Opportunity Engine | **Implemented & tested** |
| 2G | Automated Trading (paper-first) | **Implemented & tested** |

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

### Enhancement — buy/sell direction + user-configured scan coins
- New `app/services/whale_tracker/direction.py` — `classify_direction(chain, from, to)`:
  BUY/SELL (high confidence) when a known DEX router (Uniswap V2/V3, Sushi, 1inch) is involved;
  BUY/SELL (medium) on known major exchange wallet deposits/withdrawals; otherwise ⚪ Transfer (no claim).
- Providers now extract BTC `from`/`to` (mempool/Blockstream `prevout`/`vout`) and tag every `WhaleTransfer`
  with `direction` + `direction_confidence`; stored on `WhaleTransaction` and shown in `/whale`, 🐋 menu, and whale alerts.
- New `WhalePreference` model + `whale_preferences` table; `get_user_scan_symbols()` / `set_user_scan_symbols()`
  in `assets.py`. A user with no prefs scans the full registry.
- New "🔧 Set scan coins" inline button (🐋 menu + `/whale`) → `WhaleStates.waiting_for_symbols` prompt;
  accepts comma-separated symbols, `all`/`none` to reset; validates against the registry.
- `whale_tracker` `_providers`/`fetch_all`/`get_whales`/`fetch_and_store` now accept `symbols` to restrict scanning.
- `app/main.py` — `ensure_additive_columns()` idempotently adds `whale_transactions.direction`,
  `direction_confidence` to existing DBs (create_all cannot alter existing tables).
- Tests: `tests/test_whale.py` grown to 39 total (direction classifier, BTC address extraction,
  ERC-20 DEX B/S, preference set/get, symbols-filtered store).

---

## 2C — AI Trading Assistant ✅

Built a quantitative and AI-driven trade analysis assistant that computes multi-timeframe indicators on factual market data, synthesizes structured setups (Entry, Stop Loss, Take Profit 1/2, Risk/Reward, Invalidation rules), and provides a guided interactive Telegram experience.

### New files
- `app/database/models/assistant.py` — `TradingStrategy`, `StrategyVersion`, and `TradeAnalysis`.
- `app/services/assistant/`:
  - `__init__.py` — package exports.
  - `indicators.py` — pure-Python technical indicator engine: SMA, EMA, RSI (Wilder's smoothing), MACD (12, 26, 9), ATR (14), Bollinger Bands (20, 2), dynamic Support & Resistance pivot detection, and deterministic trend bias evaluation.
  - `klines.py` — multi-timeframe candle fetcher (`15m`, `1h`, `4h`, `1d`) with Binance spot API -> TwelveData -> CoinGecko OHLC -> synthetic fallback.
  - `strategies.py` — preset strategy definitions (*Trend Following / Pullback*, *Breakout & Momentum*, *Mean Reversion / Counter-Trend*, *Full Technical Diagnostic*) with DB seeding.
  - `analyzer.py` — AI trade plan generator via `call_llm` (Groq/OpenAI) + strict JSON extraction + deterministic mathematical fallback engine.
  - `assistant_service.py` — orchestration pipeline, resolution-aware caching (`TradeAnalysis`), and Telegram HTML card formatting.
- `app/bot/handlers/assistant.py` — `/trade` and `/assistant` commands, custom coin FSM, timeframe & strategy selection callbacks, refresh analysis action.
- `tests/test_assistant.py` — 15 unit tests for indicators, strategies, JSON parsing, fallback setups, and message rendering.

### Modified files
- `app/database/models/__init__.py` — import & export new assistant models; add relationships to `User`.
- `app/services/market_digest/digest_service.py` — enhanced `call_llm` to accept custom `system_prompt`.
- `app/bot/states.py` — added `AssistantStates.waiting_for_symbol`.
- `app/bot/keyboards.py` — added `🎯 Assistant` button to main menu with updated layout, plus inline keyboard builders (`assistant_assets_keyboard`, `assistant_timeframe_keyboard`, `assistant_strategy_keyboard`).
- `app/bot/handlers/menu.py` — added `🎯 Assistant` button handler and included it in FSM cancel set.
- `app/bot/dispatcher.py` — registered `assistant.router`.
- `app/main.py` — seed preset strategies on startup; gracefully close `kline_fetcher` on shutdown.

### Tests
- Full test suite: **54 tests passed in 24s** (`.\venv\Scripts\python.exe -m pytest tests -q`).

### Fix (2026-09-16) — HTML-escaping in analysis card
- Symptom: after selecting an analysis framework, the bot "stopped" and never sent the trade setup card.
- Root cause: AI-generated `reasoning`/`invalidation` text leaked raw `<`/`>` (e.g. `EMA 20 < EMA 50`), which Telegram's HTML parse mode rejects (`can't parse entities: Unsupported start tag ""`). The final `edit_text` in `cb_run_analysis` raised, so nothing was rendered.
- Fix:
  - `app/services/assistant/assistant_service.py` — `format_trade_setup_message` now HTML-escapes reasoning, invalidation, symbol, timeframe, strategy name, and bias.
  - `app/bot/handlers/assistant.py` — escape symbol/timeframe/strategy name in the "⏳ Analyzing…" status text; call `callback.answer()` before the long analysis (button no longer spins) with `&amp;` in the status copy; error path escapes symbol too.
- Verified live: reproduced the exact card against the live bot — `editMessageText` returns 200 (was 400). Full suite still **54 passed**.

### 2026-09-16 — User-defined custom strategies
- Users can now create their own trading strategies through the Assistant flow: tap **➕ New Strategy** from the framework selection screen, send a name, then describe the rules.
- The AI receives the custom rules verbatim when building the trade plan — works exactly like the built-in frameworks.
- Strategy key scheme: `custom_{id}` where id is the `TradingStrategy` primary key.
- New DB helpers in `strategies.py`: `add_user_strategy`, `get_user_strategies`, `delete_user_strategy`, `get_strategy_definition_any` (async, resolves both presets and custom rows), `is_custom_key`, `strategy_definition_from_row`.
- `assistant_service.get_or_create_trade_setup` now uses `get_strategy_definition_any` so custom strategy names appear in the trade card.
- `asst_tf` handler now fetches the user's custom strategies and passes them to the keyboard builder; `asst_new_strat` enters a two-step FSM (`waiting_for_strategy_name` → `waiting_for_strategy_rules`); `asst_manage_strats` shows a list with delete buttons; `asst_del:` removes a strategy and refreshes the list.
- `assistant_strategy_keyboard` in `keyboards.py` extended: shows custom strategies as ⭐ entries between the four presets and the ➕ button; `assistant_manage_keyboard` added for the management screen.
- Added 5 new unit tests (is_custom_key, from_row, get_definition_any fallback); full suite: **59 passed in 17.8s**.

### 2026-09-17 — Strategy document import + entry-verdict replies
- Users can now upload their strategy as a **PDF / DOCX / TXT** file via the **📄 Import Strategy Document** button on the strategy selection screen (up to 10 MB).
- A single document may contain several strategies: the AI carefully splits out each one, preserving full entry/exit logic, then imports each as its own selectable ⭐ strategy.
- New parsing module `app/services/assistant/document_parser.py` (`pypdf`, `python-docx`); new `import_strategies_from_document` in `strategies.py` runs an LLM categorization pass with a heading-based heuristic fallback when the LLM is unreachable.
- Trade cards now carry an **entry verdict**: when current indicators satisfy the strategy's entry signals the card says ✅ "Conditions favour entry — <reason>"; when they do not, the card says 🚫 "Market conditions do NOT favour entry right now" with a "Why no entry" rationale and no trade levels.
- `TradeSetup` gained `can_enter` / `entry_reason`; deterministic evaluators `evaluate_entry` (live) and `evaluate_entry_from_data` (cache reads) in `analyzer.py`; the LLM JSON schema now requests `can_enter` + `entry_reason`.
- Deps added: `pypdf`, `python-docx`.
- 16 new unit tests (document parsing, doc splitting, JSON array extract, entry verdicts, no-entry card); full suite: **75 passed in 11.5s**.

### 2026-09-17 — Resilient document import (fix)
- Document import previously hard-failed with "Could not parse the AI categorization of your document" when the LLM returned no/broken JSON (e.g. provider unreachable or truncated reply).
- `import_strategies_from_document` now runs the deterministic heading-based splitter first and treats the LLM categorization as a best-effort upgrade — the import can never fail on an AI response anymore.
- `_extract_json_array` now accepts a raw top-level array, wrapper objects with several key names, and fenced/truncated output.
- `_fallback_document_split` now also recognizes numbered headings (`1. Momentum Breakout`) and Markdown headings with cleaner name extraction.
- 3 new unit tests; full suite: **78 passed in 13.5s**.

## 2D — Web3 Portfolio ✅

### Scope
- Connect **public EVM wallet addresses only** (never seed/private keys). Balances aggregated for trading/analysis display in Telegram.

### New files
- `app/database/models/wallet.py` — `Wallet` (user_id, address, network, label, total_value_usd) + `WalletBalance` (per-token quantity/price/value), unique per (user, network, address).
- `app/services/wallet/providers.py` — `WalletProvider` abstraction: `CovalentWalletProvider` (multi-chain, COVALENT_API_KEY) + `PublicRpcProvider` keyless fallback (ETH native balance via public RPC + CoinGecko price). `normalize_network`, `is_valid_address`, `parse_covalent_items`.
- `app/services/wallet/wallet_service.py` — add/remove/list wallets, `refresh_wallet`, `refresh_all_wallets`, `format_wallet_summary`.
- `app/services/wallet/__init__.py`.
- `app/bot/handlers/wallet.py` — `/wallet` command + inline flow: connect (FSM `WalletStates.waiting_for_address`, accepts `ADDRESS [network] [label]`), list/remove with confirm, refresh.

### Modified files
- `app/core/config.py` — `COVALENT_API_KEY`, `WALLET_REFRESH_MINUTES` (default 30, min 5).
- `app/database/models/__init__.py` — register `Wallet`/`WalletBalance` + `User.wallets` relationship.
- `app/bot/keyboards.py` — 👛 Wallet main-menu button (layout 3/3/3/2), `wallet_menu_keyboard`, `wallet_remove_list_keyboard`, `wallet_remove_confirm_keyboard`.
- `app/bot/states.py` — `WalletStates.waiting_for_address`.
- `app/bot/handlers/menu.py` — 👛 Wallet menu handler + FSM cancel set.
- `app/bot/dispatcher.py` — register wallet router.
- `app/main.py` — background `run_wallet_refresh` job (every `WALLET_REFRESH_MINUTES`); close wallet provider on shutdown.

### Behavior
- Default network = `ethereum`; supported: ethereum, polygon, bsc, arbitrum, optimism, avalanche, base.
- Without `COVALENT_API_KEY` the fallback shows the native ETH balance only (tokens need the key).
- Summary shows per-wallet total USD + top-10 assets with values; Mini App visualization planned in Phase 2E.
- 10 new unit tests (network aliases, address validation, Covalent payload parsing, summary formatting); full suite: **88 passed in 10.5s**.

## 2F — Opportunity Engine ✅

### Scope
- Deterministic weighted catalyst scoring per portfolio asset (signed −100…+100), aligned to alert priorities (LOW/MEDIUM/HIGH/CRITICAL), with an optional AI interpretation. Mini App (Phase 2E) still deferred.

### New files
- `app/database/models/opportunity.py` — `OpportunitySignal` (per-component catalyst evidence) + `OpportunityScore` (latest signed total per symbol, unique, with priority).
- `app/database/models/exchange.py` — `ListingEvent` (new listings recorded by the monitor for the listing signal).
- `app/services/opportunity/signals.py` — `OpportunityComponent` dataclass + collectors: `news_signal` (NewsAnalysis impact/confidence), `market_signal` (volume 24h-surge + momentum from one 4h candle fetch), `social_signal` (Reddit/RSS mention count), `listing_signal` (24h `ListingEvent` rows), `whale_signal` (24h whale USD inflows vs outflows), `liquidity_signal` (registry tier; stablecoins excluded).
- `app/services/opportunity/engine.py` — `compute_opportunity`, `persist_opportunity`, `refresh_opportunities` (background scan over all portfolio symbols), `get_latest_scores`, `priority_for`, `total_from_components`, `format_opportunities`, `ai_note_for` (call_llm summary).
- `app/bot/handlers/opportunities.py` — `/opportunities` + menu entry (fast cached path) and 🔄 Recompute callback (live scan over the user's portfolio).

### Modified files
- `app/core/config.py` — `OPPORTUNITY_SCAN_MINUTES` (default 15, min 5).
- `app/database/models/__init__.py` — register `ListingEvent`, `OpportunitySignal`, `OpportunityScore`.
- `app/services/exchange_monitor/listing_monitor.py` — records a `ListingEvent` (normalized base symbol via `_base_symbol`) for every newly detected listing.
- `app/bot/keyboards.py` — 💡 Opportunities main-menu button (layout now 3/3/3/3), `opportunity_keyboard`.
- `app/bot/handlers/menu.py` — 💡 Opportunities menu handler + FSM cancel set.
- `app/bot/dispatcher.py` — register opportunities router.
- `app/main.py` — `run_opportunity_scan` job (every `OPPORTUNITY_SCAN_MINUTES`).

### Scoring weights
- news 20 · volume 20 · momentum 15 · social 15 · listing 15 · whale 10 · liquidity 5.

### Behavior
- Each component: `score = weight × strength(0..1) × direction(±1/0)`. Total clamped to [−100, 100]; priority tiers: CRITICAL ≥60, HIGH ≥40, MEDIUM ≥20, else LOW.
- Command/menu reads **cached** scores (fast); 🔄 Recompute runs a live scan (klines + social + news read) over the user's assets.
- Background job keeps scores warm for every portfolio symbol; listing events feed the listing signal automatically.
- **Auto-alerts (2F follow-up):** when a background scan lifts a score into HIGH or CRITICAL (first elevation, or HIGH → CRITICAL upgrade), a 🚨 opportunity alert is pushed to every `pro`/`premium` user and a `Notification` row is stored. `refresh_opportunities` returns the list of newly elevated scores; `is_elevated` guards against re-sending while a level stays elevated.
- 18 unit tests (priority tiers, weighted totals, clamping, component scoring, formatting, elevation logic, listing base-symbol normalization); full suite: **109 passed in 34s**.

---

## Next up: Phase 3 — payments/tiering · notification_preferences wiring

---

## 2G — Automated Trading (paper-first core) 🔶

### Scope
Phase 2E is taken by the Mini App, so this hardening phase is logged as **2G**. Principles from the architecture brief:
- The **AI never decides or executes**. A deterministic strategy engine produces structured rules → plan → then (risked) proposals; AI only interprets if added later.
- Every proposal has to pass the **RiskEngine gate** before the execution layer sees it.
- All execution is **PAPER_ONLY** — nothing produced today can touch a live broker.

### New files
- `app/services/trading/indicators.py` — deterministic Stochastic (`calculate_stochastic` → %K/%D + aligned series) and Fibonacci retracement levels (`fibonacci_levels`, 0/0.236/0.382/0.5/0.618/0.786/1).
- `app/services/trading/strategy_engine.py` — rule engine: `MarketContext`/`build_market_context`, `StrategySpec`, `preset_specs()` (trend_pullback, breakout, mean_reversion, momentum_reversal, general), `spec_from_definition` (preset keys or legacy free-text), a `_CHECKS` registry (~24 deterministic checks incl. RSI, EMA alignment, MACD histogram, BB reaction/width, stochastic levels+crossovers, fib zone, volume surge, swing highs/lows, min R:R, market staleness), `_MIRROR` mapping for SHORT flip, and `evaluate_signal` (setup confirmed only when **all** entry rules pass; `_heuristic_entry` fallback for rules-free custom strategies).
- `app/services/trading/trade_plan.py` — machine-readable `TradePlan` (`plan_id`, direction, entry zone, stop, TP1/TP2, invalidation, R:R, stage) with `paper_only: true` asserted in serialization; `build_trade_plan` derives levels deterministically from the confirmed signal + stop/take rule shapes.
- `app/services/trading/risk.py` — `RiskEngine` gate (8 checks: market-open/freshness, symbol supported, valid strategy setup, no duplicate position, sound stop, position sizing feasible without leverage, min R:R ≥ 1, daily loss limit) + `position_size` sizing to the risk budget.
- `app/services/trading/manager.py` — `PlanManager` state machine (ANALYSING → SETUP_FOUND → RISK_CHECK → PAPER_PENDING → PAPER_OPEN/REJECTED) with `evaluate` / `assess` / `commit` / `monitor`.
- `app/execution/base.py` — `ExecutionGateway` ABC + `ExecutionResult`.
- `app/execution/paper.py` — pure, DB-free paper engine: `PaperState`/`PaperPositionState`, `PaperExecutionEngine` (zone fill, duplicate guard, fees, SL/TP/invalidation triggers, P&L, win rate, profit factor).
- `app/execution/gateway.py` — `PaperGateway` persisting paper fills/positions and `mark_account` for the monitor.
- `app/services/trading/backtester.py` — deterministic walk-forward backtest (rolling window evaluation, zone-fill intrabar SL/TP, one position at a time) → `BacktestReport.summary()` (trades, win rate, profit factor, net P&L, max drawdown, avg holding, consecutive losses).
- `app/database/models/paper.py` — `PaperAccount` ($10k default), `PaperPosition`, `PaperFill`.

### Modified files
- `app/database/models/__init__.py` — register paper models; `User.paper_accounts` relationship.

### Tests
- `tests/test_trading.py` (28), `tests/test_paper.py` (13), `tests/test_backtester.py` (4), `tests/test_monitor.py` (4), `tests/test_api.py` (+3 trading-endpoint): full suite **197 passed**. Includes direction mirroring, per-rule results, market staleness, position sizing budget, fee accounting, TP/SL ordering, backtest report shape, monitor presets, and the `/api/trading` read-model auth.

### Monitor wiring (shipped)
- `app/services/trading/monitor.py` — `TradingMonitor.scan_once` (all portfolio symbols × armed presets → PlanManager → paper open + 📈 Telegram card) and `mark_all` (live prices → close STOP/TP → 🛑/🎯 Telegram close alerts).
- `app/main.py` — scheduler jobs `TRADING_SCAN_MINUTES` (15) + `TRADING_MARK_MINUTES` (5); config vars added.
- `app/api/routes.py` `GET /api/trading` (auth-gated read-model: strategies, paper account, open/closed positions) + Mini App **📈 Trading** tab (PAPER-ONLY badge, balance, open positions, recent closed, armed strategies), placed after Opportunities per the "not Auto Trading first" rule.

### Remaining for 2G
- Live broker adapters are deliberately excluded: nothing in this phase can touch real funds (`execution` only ships the paper gateway).
- Optional live-env authorization flow + AI interpretation layer (currently pure deterministic).

---

## 2E — Telegram Mini App ✅

### Scope
A Telegram Mini App (web front end over the FastAPI) that renders the user's live data with a signed, replay-safe auth flow. Phase order note: 2E was deferred behind 2F and shipped after it.

### New files
- `app/api/security.py` — Telegram WebApp `initData` HMAC-SHA256 validation (search-vector key from bot token, 24h freshness window) + short-lived signed app tokens (`issue_token`/`parse_token`, HMAC over `SECRET_KEY`).
- `app/api/routes.py` — `POST /api/auth` (exchange initData for app token, auto-creates user via `get_or_create_user`) and `GET /api/dashboard` (single payload: user/plan, priced portfolio + P&L, wallets + balances, cached opportunity scores, active alerts, recent notifications, latest news analyses). Auth via `X-App-Token` header; read-only.
- `app/webapp/index.html`, `app/webapp/app.js`, `app/webapp/styles.css` — zero-build SPA (dark crypto theme, light-mode via media query), tabs: Overview / Wallets / Opportunities / Alerts / News; uses `window.Telegram.WebApp.initData`, `expand()`/`ready()`, pulled refresh button.
- `tests/test_api.py` — initData validation (valid/tampered/expired/wrong-token/placeholder), token round-trip + expiry, serializers, `mini_app_url` policy (https/localhost only).

### Modified files
- `app/core/config.py` — `PUBLIC_BASE_URL` (default `http://localhost:8000`) + `MINI_APP_TOKEN_TTL_HOURS` (default 24).
- `app/bot/keyboards.py` — `mini_app_url()` helper + 🛰 Mini App WebApp reply-button when `PUBLIC_BASE_URL` is https or localhost (Telegram refuses non-HTTPS remote URLs).
- `app/main.py` — mounts the SPA at `/app` (`StaticFiles` + explicit `/app` FileResponse) and registers the `/api` router.

### Behavior
- Opening the 🛰 Mini App button boots Telegram-injected initData → `/api/auth` → token → `/api/dashboard`. Outside Telegram the page shows a friendly error.
- `PUBLIC_BASE_URL` must point at the deployed HTTPS host (e.g. Render) for the button to appear and work; default localhost target supports dev via BotFather/localhost preview.
- Dashboard reads only — mutations stay in the bot (connect wallets, set alerts, recompute scores). Prices fetched live via `price_fetcher`; everything else cached by the background jobs.
- 16 new unit tests; full suite: **125 passed in 23s**.