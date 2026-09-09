from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.services.market_digest.digest_service import generate_digest

router = Router(name="digest_handlers")

@router.message(Command("digest"))
async def cmd_digest(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )

    await message.answer("🧠 Generating market digest...")
    digest = await generate_digest(session, user.id)
    await message.answer(digest, parse_mode="HTML")
