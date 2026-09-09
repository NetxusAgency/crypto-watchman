from aiogram import Bot, Dispatcher
from app.core.config import settings
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.handlers import base, portfolio, alerts, analytics, sentiment, whale, digest, menu, callbacks

# Initialize Bot
bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

# Initialize Dispatcher
dp = Dispatcher()

# Register Middleware
dp.update.outer_middleware(DbSessionMiddleware())

# Register Routers
dp.include_router(base.router)
dp.include_router(portfolio.router)
dp.include_router(alerts.router)
dp.include_router(analytics.router)
dp.include_router(sentiment.router)
dp.include_router(whale.router)
dp.include_router(digest.router)
dp.include_router(menu.router)
dp.include_router(callbacks.router)
