import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import (
    assistant_assets_keyboard,
    assistant_strategy_keyboard,
    assistant_timeframe_keyboard,
    back_button,
    cancel_button,
)
from app.bot.states import AssistantStates
from app.services import db_service
from app.services.assistant import (
    format_trade_setup_message,
    get_or_create_trade_setup,
    get_strategy_definition,
)

logger = logging.getLogger("crypto_watchman.assistant_handlers")

router = Router()


@router.message(Command(commands=["trade", "assistant"]))
async def cmd_assistant(message: Message, session: AsyncSession):
    user = await db_service.get_user(session, message.from_user.id)
    if not user:
        user = await db_service.create_user(
            session, message.from_user.id, message.from_user.username
        )

    portfolio = await db_service.get_portfolio(session, user.id)
    symbols = [p.symbol for p in portfolio]

    text = (
        "🎯 <b>AI Trading Assistant</b>\n\n"
        "Analyze technical setups with multi-timeframe OHLCV candles, "
        "momentum & trend indicators (RSI, EMA, MACD, ATR), and disciplined risk management.\n\n"
        "Choose an asset from your portfolio or test a custom coin:"
    )
    await message.answer(text, reply_markup=assistant_assets_keyboard(symbols), parse_mode="HTML")


@router.callback_query(F.data == "asst_back_assets")
async def cb_back_assets(callback: CallbackQuery, session: AsyncSession):
    user = await db_service.get_user(session, callback.from_user.id)
    symbols = []
    if user:
        portfolio = await db_service.get_portfolio(session, user.id)
        symbols = [p.symbol for p in portfolio]

    text = "🎯 <b>AI Trading Assistant</b>\n\nSelect an asset to analyze:"
    await callback.message.edit_text(text, reply_markup=assistant_assets_keyboard(symbols), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "asst_custom")
async def cb_custom_symbol(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AssistantStates.waiting_for_symbol)
    await callback.message.edit_text(
        "🔤 <b>Enter Asset Symbol</b>\n\n"
        "Type any cryptocurrency or forex ticker (e.g. <code>BTC</code>, <code>SOL</code>, <code>ETH</code>, <code>EURUSD</code>):",
        reply_markup=cancel_button(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AssistantStates.waiting_for_symbol)
async def process_custom_symbol(message: Message, state: FSMContext):
    sym = message.text.strip().upper().replace("/", "")
    if not sym.isalnum() or len(sym) > 10:
        await message.answer(
            "⚠️ Invalid symbol format. Please enter a standard ticker like <code>BTC</code> or <code>EURUSD</code>:",
            reply_markup=cancel_button(),
            parse_mode="HTML",
        )
        return

    await state.clear()
    await message.answer(
        f"⏱ <b>Select Timeframe for {sym}</b>\n\nChoose the chart resolution to evaluate:",
        reply_markup=assistant_timeframe_keyboard(sym),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("asst_sym:"))
async def cb_select_symbol(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"⏱ <b>Select Timeframe for {symbol}</b>\n\nChoose the chart resolution to evaluate:",
        reply_markup=assistant_timeframe_keyboard(symbol),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("asst_tf:"))
async def cb_select_timeframe(callback: CallbackQuery):
    parts = callback.data.split(":")
    symbol = parts[1]
    timeframe = parts[2] if len(parts) > 2 else "1h"

    await callback.message.edit_text(
        f"🧭 <b>Select Strategy for {symbol} ({timeframe.upper()})</b>\n\n"
        f"Choose an analysis framework for the assistant to apply:",
        reply_markup=assistant_strategy_keyboard(symbol, timeframe),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("asst_run:"))
async def cb_run_analysis(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    symbol = parts[1]
    timeframe = parts[2]
    strategy_key = parts[3]
    force_refresh = len(parts) > 4 and parts[4] == "refresh"

    strat_def = get_strategy_definition(strategy_key)

    user = await db_service.get_user(session, callback.from_user.id)
    user_id = user.id if user else None

    # Status notice while computing
    await callback.message.edit_text(
        f"⏳ <i>Analyzing <b>{symbol}</b> ({timeframe.upper()}) using {strat_def.name}...\n"
        f"Fetching OHLCV candles, computing RSI/EMA/MACD/ATR & generating trade plan.</i>",
        parse_mode="HTML",
    )

    try:
        setup = await get_or_create_trade_setup(
            session=session,
            symbol=symbol,
            timeframe=timeframe,
            strategy_key=strategy_key,
            user_id=user_id,
            force_refresh=force_refresh,
        )
        msg_text = format_trade_setup_message(setup)

        actions = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Refresh Analysis",
                        callback_data=f"asst_run:{symbol}:{timeframe}:{strategy_key}:refresh",
                    ),
                    InlineKeyboardButton(
                        text="⏱ Change Timeframe",
                        callback_data=f"asst_sym:{symbol}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🧭 Change Strategy",
                        callback_data=f"asst_tf:{symbol}:{timeframe}",
                    ),
                    InlineKeyboardButton(
                        text="◀ Main Menu",
                        callback_data="menu_main",
                    ),
                ],
            ]
        )
        await callback.message.edit_text(msg_text, reply_markup=actions, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to generate assistant analysis for {symbol}: {e}")
        await callback.message.edit_text(
            f"❌ An error occurred while analyzing <b>{symbol}</b>. Please try again in a moment.",
            reply_markup=back_button(),
            parse_mode="HTML",
        )

    await callback.answer()
