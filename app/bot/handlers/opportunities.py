from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import opportunity_keyboard
from app.services import db_service
from app.services.opportunity import (
    ai_note_for,
    format_opportunities,
    get_latest_scores,
    refresh_opportunities,
)

router = Router(name="opportunity_handlers")


async def _portfolio_symbols(session: AsyncSession, user_id: int) -> list[str]:
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=user_id,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    return [p.symbol for p in portfolio]


def _filtered_scores(scores: list[dict], symbols: list[str]) -> list[dict]:
    if not symbols:
        return scores[:5]
    by_symbol = {s["symbol"]: s for s in scores}
    return [by_symbol[s] for s in symbols if s in by_symbol]


async def _show_panel(message: Message, session: AsyncSession) -> None:
    symbols = await _portfolio_symbols(session, message.from_user.id)
    scores = _filtered_scores(await get_latest_scores(session), symbols)

    if not scores:
        if not symbols:
            await message.answer(
                "💡 <b>Trade Opportunities</b>\n\n"
                "Your portfolio is empty — the engine scans your assets for catalysts.\n"
                "Add assets with /add_asset, then tap 🔄 Recompute.",
                parse_mode="HTML",
            )
            return
        await message.answer("🔄 Scanning market catalysts for your portfolio…", parse_mode="HTML")
        await refresh_opportunities(session, symbols)
        scores = _filtered_scores(await get_latest_scores(session), symbols)

    if not scores:
        await message.answer(
            "💡 <b>Trade Opportunities</b>\n\nNo catalysts detected for your assets yet.",
            parse_mode="HTML",
            reply_markup=opportunity_keyboard(),
        )
        return

    ai_note = await ai_note_for(scores)
    await message.answer(
        format_opportunities(scores, ai_note),
        parse_mode="HTML",
        reply_markup=opportunity_keyboard(),
    )


@router.message(Command("opportunities"))
async def cmd_opportunities(message: Message, session: AsyncSession):
    await _show_panel(message, session)


@router.callback_query(F.data == "opp_refresh")
async def cb_opp_refresh(callback: CallbackQuery, session: AsyncSession):
    symbols = await _portfolio_symbols(session, callback.from_user.id)
    if not symbols:
        await callback.message.edit_text(
            "💡 <b>Trade Opportunities</b>\n\nPortfolio is empty. "
            "Add assets with /add_asset first.",
            parse_mode="HTML",
            reply_markup=opportunity_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text("🔄 Recomputing scores & catalysts…", parse_mode="HTML")
    await callback.answer()
    await refresh_opportunities(session, symbols)
    scores = _filtered_scores(await get_latest_scores(session), symbols)
    ai_note = await ai_note_for(scores)
    await callback.message.edit_text(
        format_opportunities(scores, ai_note),
        parse_mode="HTML",
        reply_markup=opportunity_keyboard(),
    )