from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import db_service
from app.services.news.news_service import build_user_news_report
from app.bot.keyboards import back_button

router = Router(name="news_handlers")


@router.message(Command("news"))
async def cmd_news(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )

    report = await build_user_news_report(session, user.id)
    if report is None:
        await message.answer(
            "📭 Your portfolio is empty. Add assets with /add_asset first.",
            reply_markup=back_button(),
        )
        return

    await message.answer("🔍 Scanning news sources... (can take up to 30s)")
    await message.answer(report, parse_mode="HTML", reply_markup=back_button())