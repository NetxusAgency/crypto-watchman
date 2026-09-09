from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.services.sentiment.sentiment_monitor import sentiment_monitor

router = Router(name="sentiment_handlers")

@router.message(Command("sentiment"))
async def cmd_sentiment(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )

    portfolio = await db_service.get_portfolio(session, user.id)

    if not portfolio:
        await message.answer(
            "📭 Your portfolio is empty.\n"
            "Add assets with /add_asset to track their social sentiment.",
            parse_mode="HTML"
        )
        return

    await message.answer("🔍 Scanning Reddit and news for mentions...")

    results = []
    for item in portfolio:
        mention = await sentiment_monitor.get_mention_count(item.symbol)
        results.append(mention)

    lines = ["📢 <b>Social Sentiment</b>\n"]
    for r in results:
        icon = "🔥" if r["total"] >= 10 else "📈" if r["total"] >= 3 else "💤"
        lines.append(
            f"{icon} <b>{r['symbol']}</b> — Reddit: {r['reddit']} | News: {r['news']} | <b>Total: {r['total']}</b>"
        )

    lines.append("\n<i>Mentions in r/cryptocurrency and crypto news feeds (last 24h).</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")
