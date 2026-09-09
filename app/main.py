import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

    # 3. Start Telegram Bot Polling
    bot_task = None
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_TOKEN != "your_telegram_bot_token_here":
        try:
            bot_task = asyncio.create_task(dp.start_polling(bot))
            logger.info("Telegram Bot Polling started successfully.")
        except Exception as e:
            logger.error(f"Failed to start Telegram Bot Polling: {e}")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not configured or is set to default. Bot polling will not be started.")

    yield
    
    # Cleanup on shutdown
    logger.info("Shutting down services...")
    if bot_task:
        await dp.stop_polling()
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

@app.get("/")
@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Crypto & Forex Watchman API",
        "bot_configured": settings.TELEGRAM_BOT_TOKEN != "your_telegram_bot_token_here"
    }
