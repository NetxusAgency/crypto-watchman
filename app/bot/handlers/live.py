"""Live-trade execution callbacks.

Handles the inline ✅ Confirm / ❌ Reject buttons sent with a trade proposal.
Nothing executes automatically — the user must tap a button first.

The callback is acknowledged immediately (clears Telegram's loading spinner) so
the user always gets visual feedback, then the real broker work runs under a
hard time cap and the proposal message is re-written with the final outcome.
"""
import asyncio
import html
import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import db_service
from app.services.trading.live import LiveExecutionService, LiveExecutionResult

logger = logging.getLogger("crypto_watchman.live_trading")

router = Router(name="live_exec_handlers")

# A broker round-trip must never leave a tapped button unanswered. If the full
# confirm path (positions + order placement) exceeds this, the user is told the
# operation timed out and to check cTrader directly.
_DECISION_TIMEOUT_SECONDS = 45


async def _finalize(callback: CallbackQuery, text: str) -> None:
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
    except Exception:  # noqa: BLE001
        try:
            await callback.message.answer(text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            logger.warning("live decision: could not update %s", text[:80])


async def _apply_decision(service, session, *, user, trade_id: int, decision: str) -> LiveExecutionResult:
    if decision == "confirm":
        return await service.confirm_trade(session, user=user, trade_id=trade_id)
    if decision == "reject":
        return await service.reject_trade(session, user=user, trade_id=trade_id)
    raise ValueError("Unknown decision")


@router.callback_query(F.data.startswith("liveexec:"))
async def live_decision(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    if len(parts) != 3:
        try:
            await callback.answer("Malformed decision.")
        except Exception:  # noqa: BLE001
            pass
        return
    _, trade_id, decision = parts

    try:
        await callback.answer("⏳ Working…")
    except Exception:  # noqa: BLE001
        pass

    try:
        user = await db_service.get_or_create_user(
            session=session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("live decision: user lookup failed: %s", e)
        await _finalize(callback, "⚠️ Could not identify your account. Try again in a moment.")
        return

    service = LiveExecutionService()
    try:
        result = await asyncio.wait_for(
            _apply_decision(service, session, user=user, trade_id=int(trade_id), decision=decision),
            timeout=_DECISION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("live decision timed out after %ss (trade=%s)", _DECISION_TIMEOUT_SECONDS, trade_id)
        await _finalize(
            callback,
            "⏰ <b>Live execution timed out</b>\n\n"
            "The broker did not confirm in time. Check your open positions in "
            "cTrader — the order may still have gone through.",
        )
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("live decision failed (trade=%s): %s", trade_id, e)
        await _finalize(callback, f"⚠️ <b>Live execution error</b>\n\n<code>{html.escape(str(e))}</code>")
        return

    if result.allowed:
        text = (
            f"✅ <b>Trade confirmed & executed</b>\n\n{result.reason}"
            if decision == "confirm"
            else f"🚫 <b>Trade declined</b>\n\n{result.reason}"
        )
    else:
        text = f"⛔ <b>Not executed</b>\n\n{result.reason}"
    await _finalize(callback, text)