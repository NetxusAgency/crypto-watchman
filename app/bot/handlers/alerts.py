from aiogram import Router, html
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service

router = Router(name="alerts_handlers")

@router.message(Command("alerts"))
async def cmd_alerts(message: Message, session: AsyncSession):
    """List active alerts for a user."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    alerts = await db_service.get_user_alerts(session, user.id)
    
    if not alerts:
        await message.answer(
            "📭 You don't have any active price alerts.\n\n"
            "Set one with:\n"
            "<code>/add_alert BTC above 75000</code> — price above target\n"
            "<code>/add_alert BTC below 60000</code> — price below target\n"
            "<code>/add_alert BTC move 5</code> — &plusmn;5% from entry\n"
            "<code>/add_alert BTC volume 5000000</code> — 24h volume above $5M\n"
            "<code>/add_alert BTC volatility 2</code> — 2x normal daily move\n"
            "<code>/add_alert BTC sentiment 10</code> — 24h mentions above 10\n"
            "<code>/add_alert BTC whale</code> — alert on large BTC transactions (≥10 BTC)\n"
            "<code>/add_alert BTC whale 50</code> — custom threshold (≥50 BTC)",
            parse_mode="HTML"
        )
        return
        
    alerts_text = "🔔 <b>Your Active Alerts:</b>\n\n"
    for alert in alerts:
        if alert.alert_type == "move_percent":
            trigger_desc = f"Move &plusmn;{alert.target_value}% from entry"
        elif alert.alert_type == "volume_above":
            trigger_desc = f"Volume above <code>${alert.target_value:,.0f}</code>"
        elif alert.alert_type == "volatility":
            trigger_desc = f"Volatility &gt; {alert.target_value}x normal"
        elif alert.alert_type == "sentiment_spike":
            trigger_desc = f"Sentiment spike &gt; {alert.target_value:.0f} mentions/24h"
        elif alert.alert_type == "whale_alert":
            trigger_desc = f"Whale tx &ge; {alert.target_value:.0f} {alert.symbol}"
        else:
            trigger_desc = f"{alert.alert_type.replace('price_', '').title()} <code>{alert.target_value:,.4f}</code>"
        alerts_text += (
            f"• ID: <code>{alert.id}</code> | <b>{alert.symbol}</b> — {trigger_desc}\n"
        )
    
    alerts_text += "\nTo delete an alert, use <code>/remove_alert &lt;alert_id&gt;</code>."
    await message.answer(alerts_text, parse_mode="HTML")

@router.message(Command("add_alert"))
async def cmd_add_alert(message: Message, session: AsyncSession):
    """Add a new alert."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    args = message.text.split()
    if len(args) < 4:
        await message.answer(
            "⚠️ <b>Incorrect format!</b>\n"
            "Usage: /add_alert &lt;symbol&gt; &lt;above/below/move/volume/volatility&gt; &lt;target&gt;\n"
            "Example: <code>/add_alert BTC above 70000</code>\n"
            "Example: <code>/add_alert BTC move 5</code> (&plusmn;5% from entry)",
            parse_mode="HTML"
        )
        return
        
    symbol = args[1].upper().strip()
    direction = args[2].lower().strip()
    
    if direction not in ["above", "below", "move", "volume", "volatility", "sentiment", "whale"]:
        await message.answer("❌ <b>Error:</b> Direction must be 'above', 'below', 'move', 'volume', 'volatility', 'sentiment', or 'whale'.")
        return

    # Check if the symbol is in the user's portfolio first
    portfolio = await db_service.get_portfolio(session, user.id)
    has_asset = any(item.symbol == symbol for item in portfolio)
    
    if not has_asset:
        await message.answer(
            f"⚠️ <b>Asset not in portfolio!</b>\n"
            f"You must first add <b>{symbol}</b> to your portfolio using:\n"
            f"<code>/add_asset {symbol} &lt;entry_price&gt;</code>",
            parse_mode="HTML"
        )
        return
        
    try:
        target_value = float(args[3].replace(",", ""))
        if target_value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ <b>Error:</b> Target must be a valid positive number.")
        return

    if direction == "move":
        alert_type = "move_percent"
        trigger_desc = f"Price moves <b>&plusmn;{target_value}%</b> from entry"
    elif direction == "volume":
        alert_type = "volume_above"
        trigger_desc = f"24h volume <b>above</b> <code>${target_value:,.0f}</code>"
    elif direction == "volatility":
        alert_type = "volatility"
        trigger_desc = f"Daily move exceeds <b>{target_value}x</b> normal volatility"
    elif direction == "sentiment":
        alert_type = "sentiment_spike"
        trigger_desc = f"24h mentions <b>above</b> {target_value:.0f}"
    elif direction == "whale":
        alert_type = "whale_alert"
        trigger_desc = f"Large tx &ge; {target_value:.0f} {symbol}"
    else:
        alert_type = f"price_{direction}"  # price_above or price_below
        trigger_desc = f"Price goes <b>{direction}</b> <code>{target_value:,.4f}</code>"
        
    alert = await db_service.add_alert(session, user.id, symbol, alert_type, target_value)
    
    await message.answer(
        f"🔔 <b>Alert Set Successfully!</b>\n"
        f"• ID: <code>{alert.id}</code>\n"
        f"• Asset: <b>{alert.symbol}</b>\n"
        f"• Trigger: {trigger_desc}",
        parse_mode="HTML"
    )

@router.message(Command("remove_alert"))
async def cmd_remove_alert(message: Message, session: AsyncSession):
    """Remove a specific alert."""
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "⚠️ <b>Incorrect format!</b>\n"
            "Usage: /remove_alert &lt;alert_id&gt;\n"
            "Example: <code>/remove_alert 12</code>",
            parse_mode="HTML"
        )
        return
        
    try:
        alert_id = int(args[1])
    except ValueError:
        await message.answer("❌ <b>Error:</b> Alert ID must be a valid integer.")
        return
        
    success = await db_service.remove_alert_by_id(session, user.id, alert_id)
    if success:
        await message.answer("✅ Alert removed successfully.")
    else:
        await message.answer("❌ Alert not found or does not belong to you.")
        return
