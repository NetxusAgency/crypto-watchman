from aiogram import Router, html
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service

router = Router(name="portfolio_handlers")

@router.message(Command("portfolio"))
async def cmd_portfolio(message: Message, session: AsyncSession):
    """List user's portfolio."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    portfolio = await db_service.get_portfolio(session, user.id)
    
    if not portfolio:
        await message.answer(
            "📭 Your portfolio is currently empty.\n"
            "Use /add_asset &lt;symbol&gt; &lt;entry_price&gt; to add some assets!\n"
            "Example: <code>/add_asset BTC 65000</code>",
            parse_mode="HTML"
        )
        return
        
    portfolio_text = "📊 <b>Your Portfolio Monitoring List:</b>\n\n"
    for item in portfolio:
        portfolio_text += f"• <b>{item.symbol}</b> - Entry Price: <code>{item.entry_price:,.4f}</code>\n"
        
    if user.plan == "free":
        portfolio_text += f"\nTotal Assets: {len(portfolio)}/5"
    else:
        portfolio_text += f"\nTotal Assets: {len(portfolio)} (Unlimited)"
    await message.answer(portfolio_text, parse_mode="HTML")

@router.message(Command("add_asset"))
async def cmd_add_asset(message: Message, session: AsyncSession):
    """Add/update asset in portfolio."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    # Parse arguments
    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "⚠️ <b>Incorrect format!</b>\n"
            "Usage: /add_asset &lt;symbol&gt; &lt;entry_price&gt;\n"
            "Example: <code>/add_asset BTC 65200</code> or <code>/add_asset EURUSD 1.0825</code>",
            parse_mode="HTML"
        )
        return
        
    symbol = args[1].upper().strip()
    try:
        entry_price = float(args[2].replace(",", ""))
        if entry_price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ <b>Error:</b> Entry price must be a valid positive number.")
        return
        
    # Check limit for Free tier
    portfolio = await db_service.get_portfolio(session, user.id)
    is_existing = any(item.symbol == symbol for item in portfolio)
    
    if len(portfolio) >= 5 and not is_existing and user.plan == "free":
        await message.answer(
            "⚠️ <b>Limit Reached!</b>\n"
            "Free tier is limited to 5 assets. Please upgrade to Pro or remove an asset using /remove_asset.",
            parse_mode="HTML"
        )
        return
        
    asset = await db_service.add_portfolio_asset(session, user.id, symbol, entry_price)
    
    await message.answer(
        f"✅ Monitored asset updated:\n"
        f"• Symbol: <b>{asset.symbol}</b>\n"
        f"• Entry Price: <b>{asset.entry_price:,.4f}</b>",
        parse_mode="HTML"
    )

@router.message(Command("remove_asset"))
async def cmd_remove_asset(message: Message, session: AsyncSession):
    """Remove asset from portfolio."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "⚠️ <b>Incorrect format!</b>\n"
            "Usage: /remove_asset &lt;symbol&gt;\n"
            "Example: <code>/remove_asset BTC</code>",
            parse_mode="HTML"
        )
        return
        
    symbol = args[1].upper().strip()
    success = await db_service.remove_portfolio_asset(session, user.id, symbol)
    
    if success:
        await message.answer(
            f"✅ Asset <b>{symbol}</b> and all associated alerts have been removed from your monitor.",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"❌ Asset <b>{symbol}</b> was not found in your portfolio.",
            parse_mode="HTML"
        )
