"""Live-trade execution callbacks.

Handles the inline ✅ Confirm / ❌ Reject buttons sent with a trade proposal.
Nothing executes automatically — the user must tap a button first.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import db_service
from app.services.trading.live import LiveExecutionService

router = Router(name="live_exec_handlers")


@router.callback_query(F.data.startswith("liveexec:"))
async def live_decision(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Malformed decision.")
        return
    _, trade_id, decision = parts
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    service = LiveExecutionService()
    try:
        if decision == "confirm":
            result = await service.confirm_trade(session, user=user, trade_id=int(trade_id))
        elif decision == "reject":
            result = await service.reject_trade(session, user=user, trade_id=int(trade_id))
        else:
            await callback.answer("Unknown decision.")
            return
    except Exception as e:  # noqa: BLE001
        await callback.answer("Could not process the request.")
        await callback.message.edit_text(
            f"⚠️ <b>Live execution error</b>\n\n<code>{str(e)}</code>",
            parse_mode="HTML",
        )
        return

    if result.allowed:
        text = (
            f"✅ <b>Trade confirmed & executed</b>\n\n{result.reason}"
            if decision == "confirm"
            else f"🚫 <b>Trade declined</b>\n\n{result.reason}"
        )
        await callback.answer("Done.")
        await callback.message.edit_text(text, parse_mode="HTML")
        return

    await callback.answer("Blocked.")
    await callback.message.edit_text(
        f"⛔ <b>Not executed</b>\n\n{result.reason}",
        parse_mode="HTML",
    )