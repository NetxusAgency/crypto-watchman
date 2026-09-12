import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramConflictError

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize Database Tables (auto-migration on startup)
    logger.info("Creating database tables if not exist...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified.")

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

    scheduler.add_job(run_alert_check, "interval", seconds=30)
    scheduler.add_job(run_listing_check, "interval", minutes=2)
    scheduler.add_job(run_daily_digest, "cron", hour=9, minute=0)
    scheduler.start()
    logger.info("Started internal background scheduler (alerts 30s, listings 2m, digest daily at 09:00).")

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
