import asyncio
import html
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramConflictError
from sqlalchemy import text

from app.core.config import settings
from app.core.logger import logger
from app.database.session import engine, Base, async_session_maker
from app.bot.dispatcher import bot, dp
from app.services.alerts.alert_manager import check_all_alerts
from app.services.market_digest.digest_service import generate_digest
from app.services.prices.price_fetcher import price_fetcher
from app.services.exchange_monitor.listing_monitor import listing_monitor
from app.services.sentiment.sentiment_monitor import sentiment_monitor
from app.services.whale_tracker.whale_tracker import whale_tracker
from app.services.news.client import news_http
from app.services.news import news_service
from app.services.whale_tracker.assets import seed_assets
from app.services.assistant.strategies import seed_preset_strategies
from app.services.assistant.klines import kline_fetcher
from app.services.wallet import wallet_service
from app.services.opportunity import engine as opportunity_engine
from app.services.opportunity import PRIORITY_ICONS, format_opportunity
from app.api import router as api_router

# Additive DB columns added after a table already exists (create_all cannot add
# columns to an existing table). Each entry is applied idempotently at startup.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("whale_transactions", "direction", "VARCHAR(10)"),
    ("whale_transactions", "direction_confidence", "VARCHAR(10)"),
    ("broker_connections", "mode", "VARCHAR(10)"),
    ("broker_connections", "refresh_token_enc", "TEXT"),
    ("broker_connections", "token_expires_at", "TIMESTAMP"),
]


async def ensure_additive_columns() -> None:
    for table, column, ddl in _ADDITIVE_COLUMNS:
        try:
            stmt = text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            if engine.dialect.name != "sqlite":
                # Postgres supports IF NOT EXISTS; sqlite does not.
                stmt = text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            async with engine.begin() as conn:
                await conn.execute(stmt)
        except Exception as e:  # column already exists (sqlite) or similar
            logger.debug(f"Column check {table}.{column}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Database Tables (auto-migration on startup)
    logger.info("Creating database tables if not exist...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_additive_columns()
    logger.info("Database tables verified.")

    # 1b. Seed the asset registry used by the multi-asset whale monitor
    try:
        async with async_session_maker() as session:
            created = await seed_assets(session)
            if created:
                logger.info(f"Seeded {created} default asset registry rows.")
    except Exception as e:
        logger.warning(f"Asset registry seeding skipped: {e}")

    # 1c. Seed preset trading strategies used by AI Trading Assistant
    try:
        async with async_session_maker() as session:
            strat_created = await seed_preset_strategies(session)
            if strat_created:
                logger.info(f"Seeded {strat_created} preset trading strategies.")
    except Exception as e:
        logger.warning(f"Trading strategy seeding skipped: {e}")

    # 2. Local scheduler for checking alerts (runs when Celery isn't running)
    scheduler = AsyncIOScheduler()
    async def run_alert_check():
        async with async_session_maker() as session:
            await check_all_alerts(session)

    async def run_listing_check():
        async with async_session_maker() as session:
            await listing_monitor.check_listings(session)

    async def run_daily_digest():
        from sqlalchemy import select
        from app.database.models import User
        from app.bot.dispatcher import bot
        async with async_session_maker() as session:
            stmt = select(User).where(User.plan.in_(["pro", "premium"]))
            result = await session.execute(stmt)
            users = result.scalars().all()
            for user in users:
                try:
                    digest = await generate_digest(session, user.id)
                    if digest:
                        await bot.send_message(chat_id=user.telegram_id, text=digest, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to send daily digest to User {user.id}: {e}")

    async def run_news_refresh():
        from app.services import db_service
        async with async_session_maker() as session:
            symbols = await db_service.all_portfolio_symbols(session)
            if symbols:
                await news_service.refresh_all(session, symbols)

    async def run_news_cleanup():
        async with async_session_maker() as session:
            await news_service.cleanup_expired(session)

    async def run_whale_scan():
        async with async_session_maker() as session:
            await whale_tracker.fetch_and_store(session)

    async def run_wallet_refresh():
        try:
            refreshed = await wallet_service.refresh_all_wallets(async_session_maker)
            if refreshed:
                logger.info(f"Refreshed balances for {refreshed} connected wallets.")
        except Exception as e:
            logger.warning(f"Wallet background refresh failed: {e}")

    async def run_opportunity_scan():
        try:
            async with async_session_maker() as session:
                elevated = await opportunity_engine.refresh_opportunities(session)
            if elevated:
                logger.info(
                    f"Opportunity scan: {len(elevated)} asset(s) reached "
                    f"HIGH/CRITICAL, notifying pro/premium users."
                )
                await notify_elevated_opportunities(elevated)
            else:
                logger.info("Opportunity scan: refreshed scores, no new HIGH/CRITICAL alerts.")
        except Exception as e:
            logger.warning(f"Opportunity background scan failed: {e}")

    async def run_live_scan():
        """Automated live-execution scan — never executes automatically.

        Only ever touches the confirmation path (PENDING_CONFIRM + Telegram
        ✅/❌ buttons) when the user armed the connection AND the account mode
        allows it (demo: arm is enough; live: arm + global kill-switch).
        Otherwise the scan runs in dry-run mode (full pipeline, broker
        untouched) so the user can watch setups form before enabling trading.
        Open-but-unfulfilled PLACED trades are reconciled to CLOSED first, so
        the no-duplicate rule frees itself once the broker position is gone.
        """
        try:
            from app.services.trading.live import LiveExecutionService
            from sqlalchemy import select

            async with async_session_maker() as session:
                from app.database.models import BrokerConnection, User

                rows = (
                    await session.execute(
                        select(BrokerConnection).where(BrokerConnection.is_active == True)
                    )
                ).scalars().all()
                if not rows:
                    return

                service = LiveExecutionService()
                for connection in rows:
                    _dry = not connection.is_live or (
                        connection.mode == "live" and not settings.LIVE_TRADING_ENABLED
                    )
                    user = (
                        await session.execute(
                            select(User).where(User.id == connection.user_id)
                        )
                    ).scalar_one_or_none()
                    if user is None:
                        continue
                    try:
                        await service.sync_closed(session, connection)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Live scan sync_closed failed: {e}")
                    for symbol in ["BTCUSD", "ETHUSD"]:
                        try:
                            result = await service.propose_trade(
                                session,
                                user=user,
                                connection=connection,
                                symbol=symbol,
                                strategy_key="momentum",
                                strategy_name="Momentum",
                                dry_run=_dry,
                                kline_source=kline_fetcher,
                                notify=True,
                            )
                            if result.allowed:
                                logger.info(
                                    f"Live scan {symbol}: {result.reason} "
                                    f"[dry_run={_dry}]"
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"Live scan {symbol} failed: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Live background scan failed: {e}")

    async def notify_elevated_opportunities(elevated: list[dict]):
        from sqlalchemy import select
        from app.database.models import User, Notification
        from app.bot.dispatcher import bot
        async with async_session_maker() as session:
            stmt = select(User).where(User.plan.in_(["pro", "premium"]))
            users = (await session.execute(stmt)).scalars().all()
            if not users:
                return
            for item in elevated:
                body = (
                    f"🚨 <b>{html.escape(item['symbol'])} opportunity — "
                    f"{PRIORITY_ICONS.get(item['priority'], '')} {item['priority']}</b>\n\n"
                    f"{format_opportunity(item)}\n\n"
                    "<i>Catalyst score from the Opportunity Engine. Use 🎯 Assistant for a full trade plan.</i>"
                )
                for user in users:
                    try:
                        await bot.send_message(chat_id=user.telegram_id, text=body, parse_mode="HTML")
                        session.add(
                            Notification(
                                user_id=user.id,
                                message=f"Opportunity {item['symbol']} reached {item['priority']}",
                            )
                        )
                    except Exception as e:
                        logger.error(f"Failed to send opportunity alert to User {user.id}: {e}")
                await session.commit()

    async def run_token_refresh():
        """Keep cTID OAuth access tokens fresh before they expire (lazy checks
        in live.py cover individual broker calls; this sweep catches idle ones)."""
        try:
            from app.services import db_service
            from app.services.trading.ctid_oauth import ensure_valid_token

            async with async_session_maker() as session:
                rows = await db_service.all_broker_connections(session)
                for connection in rows:
                    try:
                        await ensure_valid_token(session, connection)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            f"Token refresh failed for connection {connection.id}: {e}"
                        )
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Token refresh sweep failed: {e}")

    scheduler.add_job(run_alert_check, "interval", seconds=30)
    scheduler.add_job(run_listing_check, "interval", minutes=2)
    scheduler.add_job(run_daily_digest, "cron", hour=9, minute=0)
    scheduler.add_job(run_news_refresh, "interval", minutes=settings.NEWS_REFRESH_MINUTES)
    scheduler.add_job(run_news_cleanup, "cron", hour="*/6", minute=5)
    scheduler.add_job(run_whale_scan, "interval", minutes=settings.WHALE_SCAN_MINUTES)
    scheduler.add_job(run_wallet_refresh, "interval", minutes=settings.WALLET_REFRESH_MINUTES)
    scheduler.add_job(run_opportunity_scan, "interval", minutes=settings.OPPORTUNITY_SCAN_MINUTES)
    scheduler.add_job(run_live_scan, "interval", minutes=settings.TRADING_SCAN_MINUTES)
    scheduler.add_job(run_token_refresh, "interval", minutes=settings.CTRADER_TOKEN_REFRESH_MINUTES)
    scheduler.start()
    logger.info("Started internal background scheduler (alerts 30s, listings 2m, digest daily at 09:00, news 10m, whales 5m, wallet 30m, opportunities 15m, live trading 15m, cTID token refresh 30m).")

    # 3. Start Telegram Bot Polling (supervised, auto-restarts on crash)
    async def run_polling_worker():
        """Keep Telegram long-polling alive. Restarts with backoff if it crashes,
        so a transient error (409 conflict, network blip) can't silently kill the bot."""
        retry_delay = 5
        while True:
            try:
                await bot.delete_webhook(drop_pending_updates=True)
                await dp.start_polling(bot)
                logger.info("Bot polling session ended cleanly.")
                return
            except asyncio.CancelledError:
                logger.info("Bot polling worker cancelled (shutdown).")
                raise
            except TelegramConflictError as e:
                logger.error(
                    f"Polling conflict (409): {e}. Another instance is using the same bot "
                    "token (e.g. bot running locally too). Will retry.")
            except Exception as e:
                logger.exception(f"Bot polling crashed: {e!r}")
            logger.warning(f"Restarting bot polling in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

    bot_task = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN not in ("placeholder_token", "your_telegram_bot_token_here"):
        try:
            bot_task = asyncio.create_task(run_polling_worker())
            logger.info("Telegram Bot Polling started (supervised worker with auto-restart).")
        except Exception as e:
            logger.error(f"Failed to start Telegram Bot Polling: {e}")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not configured or is set to default. Bot polling will not be started.")

    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down services...")
    if bot_task and not bot_task.done():
        try:
            await dp.stop_polling()
        except Exception as e:
            logger.warning(f"Error stopping polling: {e}")
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        logger.info("Telegram Bot Polling stopped.")
        
    scheduler.shutdown()
    logger.info("Internal scheduler shut down.")
    
    await price_fetcher.close()
    logger.info("Price fetcher HTTP client closed.")

    await sentiment_monitor.close()
    logger.info("Sentiment monitor HTTP client closed.")
    
    await whale_tracker.close()
    logger.info("Whale tracker HTTP client closed.")
    
    await listing_monitor.close()
    logger.info("Listing monitor clients closed.")

    await news_http.close()
    logger.info("News HTTP client closed.")

    await kline_fetcher.close()
    logger.info("Assistant kline fetcher HTTP client closed.")

    await wallet_service.close_wallet_provider()
    logger.info("Wallet provider HTTP client closed.")

    await engine.dispose()
    logger.info("Database connection pool disposed.")


# Initialize FastAPI App
app = FastAPI(
    title="Crypto & Forex Watchman API",
    description="Backend API and monitor service for Crypto & Forex Watchman Telegram Bot",
    version="1.0.0",
    lifespan=lifespan
)

@app.api_route("/", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
@app.api_route("/health", methods=["GET", "HEAD", "OPTIONS"], include_in_schema=False)
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Crypto & Forex Watchman API",
        "bot_configured": settings.TELEGRAM_BOT_TOKEN != "your_telegram_bot_token_here"
    }


# Phase 2E — Telegram Mini App
_WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
async def mini_app_index():
    html_content = (_WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
    html_content = html_content.replace(
        "__BOT_USERNAME__", html.escape(settings.TELEGRAM_BOT_USERNAME)
    )
    return HTMLResponse(content=html_content)


app.include_router(api_router, prefix="/api")

# Static assets for the Mini App (served after the explicit /app route above;
# the index page is rendered above so the Telegram Login Widget gets the bot
# username baked in at request time).
app.mount("/app", StaticFiles(directory=_WEBAPP_DIR, html=False), name="mini_app")
