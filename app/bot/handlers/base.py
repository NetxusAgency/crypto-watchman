from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service

router = Router(name="base_handlers")

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    from app.bot.keyboards import main_menu_keyboard
    await message.answer(
        f"👋 Welcome, {html.quote(message.from_user.first_name)}!\n\n"
        "I'm your Crypto & Forex market monitor. Tap a button below to get started.",
        reply_markup=main_menu_keyboard(),
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handler for /help command."""
    help_text = (
        "🤖 <b>Crypto & Forex Watchman Help Guide</b>\n\n"
        "<b>Portfolio Management:</b>\n"
        "• /portfolio - Lists all monitored assets and current stats.\n"
        "• /add_asset &lt;symbol&gt; &lt;entry_price&gt; - Add/update a coin/forex pair (e.g. <code>BTC</code>, <code>ETH</code>, <code>EURUSD</code>).\n"
        "  <i>Example: /add_asset BTC 67200</i>\n"
        "• /remove_asset &lt;symbol&gt; - Stop monitoring this asset and delete its alerts.\n"
        "  <i>Example: /remove_asset BTC</i>\n\n"
        "<b>Alert Rules:</b>\n"
        "• /add_alert &lt;symbol&gt; &lt;above/below/move/volume/volatility/sentiment/whale&gt; &lt;target&gt; - Set price, percentage, volume, volatility, sentiment, or whale alerts.\n"
        "  <i>Example: /add_alert ETH above 4200</i>\n"
        "  <i>Example: /add_alert BTC move 5</i> (&plusmn;5% from entry)\n"
        "  <i>Example: /add_alert BTC sentiment 10</i> (alert when 24h Reddit+news mentions exceed 10)\n"
        "  <i>Example: /add_alert BTC whale 10</i> (alert when a BTC transaction ≥10 BTC is detected)\n\n"
        "<b>Analytics:</b>\n"
        "• /analytics - Shows PnL breakdown, best/worst performers.\n"
        "• /sentiment - Shows Reddit and news mention counts for your assets.\n"
        "• /whale - Shows recent large blockchain transactions (≥10 BTC, ≥100 ETH).\n"
        "• /digest - Generates an AI-powered market summary of your portfolio.\n\n"
        "<b>Account Settings:</b>\n"
        "• /settings - Shows your account status, active plan, and usage details.\n"
    )
    await message.answer(help_text, parse_mode="HTML")

@router.message(Command("settings"))
async def cmd_settings(message: Message, session: AsyncSession):
    """Handler for /settings command."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    portfolio = await db_service.get_portfolio(session, user.id)
    alerts = await db_service.get_user_alerts(session, user.id)
    
    if user.plan == "free":
        limit_display = f"{len(portfolio)} / 5 (Free Limit)"
        upgrade_msg = "\n<i>To upgrade your subscription, contact support. (Pro plans start at $9/mo)</i>"
    else:
        limit_display = f"{len(portfolio)} (Unlimited)"
        upgrade_msg = "\n<i>✅ You have access to all Pro features including unlimited assets and exchange listing alerts.</i>"
    
    settings_text = (
        "⚙️ <b>Your Settings</b>\n\n"
        f"• <b>Telegram ID:</b> <code>{user.telegram_id}</code>\n"
        f"• <b>Subscription Plan:</b> <code>{user.plan.upper()}</code>\n"
        f"• <b>Portfolio Assets:</b> {limit_display}\n"
        f"• <b>Active Alerts:</b> {len(alerts)}\n"
        f"{upgrade_msg}"
    )
    await message.answer(settings_text, parse_mode="HTML")
