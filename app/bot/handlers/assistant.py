import logging

from aiogram import F, Router, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import (
    assistant_assets_keyboard,
    assistant_manage_keyboard,
    assistant_strategy_keyboard,
    assistant_timeframe_keyboard,
    back_button,
    cancel_button,
)
from app.bot.states import AssistantStates
from app.services import db_service
from app.services.assistant import (
    add_user_strategy,
    delete_user_strategy,
    format_trade_setup_message,
    get_or_create_trade_setup,
    get_strategy_definition_any,
    get_user_strategies,
)

logger = logging.getLogger("crypto_watchman.assistant_handlers")

router = Router()


@router.message(Command(commands=["trade", "assistant"]))
async def cmd_assistant(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
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
async def cb_select_timeframe(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    parts = callback.data.split(":")
    symbol = parts[1]
    timeframe = parts[2] if len(parts) > 2 else "1h"

    await state.update_data(asst_symbol=symbol, asst_timeframe=timeframe)

    user_strategies = await _load_user_strategies(session, callback.from_user.id)

    await callback.message.edit_text(
        f"🧭 <b>Select Strategy for {html.quote(symbol)} ({html.quote(timeframe.upper())})</b>\n\n"
        f"Choose a preset framework or tap ⭐ one of your own strategies.\n"
        f"AI will apply the selected rules to build the trade plan.",
        reply_markup=assistant_strategy_keyboard(symbol, timeframe, user_strategies),
        parse_mode="HTML",
    )
    await callback.answer()


async def _load_user_strategies(session: AsyncSession, telegram_id: int) -> list:
    user = await db_service.get_user(session, telegram_id)
    if not user:
        return []
    return await get_user_strategies(session, user.id)


@router.callback_query(F.data == "asst_noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("asst_run:"))
async def cb_run_analysis(callback: CallbackQuery, session: AsyncSession):
    parts = callback.data.split(":")
    symbol = parts[1]
    timeframe = parts[2]
    strategy_key = parts[3]
    force_refresh = len(parts) > 4 and parts[4] == "refresh"

    user = await db_service.get_user(session, callback.from_user.id)
    user_id = user.id if user else None

    strat_def = await get_strategy_definition_any(session, strategy_key, user_id)

    # Status notice while computing
    await callback.message.edit_text(
        f"⏳ <i>Analyzing <b>{html.quote(symbol)}</b> ({html.quote(timeframe.upper())}) "
        f"using {html.quote(strat_def.name)}...\n"
        f"Fetching OHLCV candles, computing RSI/EMA/MACD/ATR &amp; generating trade plan.</i>",
        parse_mode="HTML",
    )
    await callback.answer()

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
        logger.error(f"Failed to generate assistant analysis for {symbol}: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ An error occurred while analyzing <b>{html.quote(symbol)}</b>. Please try again in a moment.",
            reply_markup=back_button(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "asst_new_strat")
async def cb_new_strategy(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AssistantStates.waiting_for_strategy_name)
    await callback.message.edit_text(
        "➕ <b>Create Your Own Strategy</b>\n\n"
        "The AI will follow your rules exactly when building the trade plan.\n\n"
        "First, send a short <b>name</b> for the strategy.\n"
        "Example: <code>Keltner Momentum</code>",
        reply_markup=cancel_button(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AssistantStates.waiting_for_strategy_name)
async def process_strategy_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 60:
        await message.answer(
            "⚠️ The name must be 1–60 characters. Please send a valid name:",
            reply_markup=cancel_button(),
        )
        return

    await state.update_data(strategy_name=name)
    await state.set_state(AssistantStates.waiting_for_strategy_rules)
    await message.answer(
        "📝 Now send the <b>strategy rules</b> the AI must follow.\n\n"
        "Include entry conditions, confirmation indicators, stop-loss placement and targets.\n\n"
        "Example:\n"
        "<code>Go long when EMA 20 crosses above EMA 50 and RSI is between 40 and 60. "
        "Enter on the cross. Stop loss at 1.5x ATR below entry. Targets at the previous swing high.</code>",
        parse_mode="HTML",
    )


@router.message(AssistantStates.waiting_for_strategy_rules)
async def process_strategy_rules(message: Message, session: AsyncSession, state: FSMContext):
    rules = (message.text or "").strip()
    if len(rules) < 20:
        await message.answer(
            "⚠️ Please describe the rules in more detail (at least 20 characters):",
            reply_markup=cancel_button(),
        )
        return
    if len(rules) > 2000:
        rules = rules[:2000]

    data = await state.get_data()
    name = data.get("strategy_name", "My Strategy")
    symbol = data.get("asst_symbol")
    timeframe = data.get("asst_timeframe")

    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    strategy = await add_user_strategy(session, user.id, name, rules)
    await state.clear()

    await message.answer(
        f"✅ Strategy <b>{html.quote(strategy.name)}</b> created!",
        parse_mode="HTML",
    )

    if symbol and timeframe:
        user_strategies = await get_user_strategies(session, user.id)
        await message.answer(
            f"🧭 <b>Select Strategy for {html.quote(symbol)} ({html.quote(timeframe.upper())})</b>\n\n"
            f"Tap ⭐ {html.quote(strategy.name)} to run the analysis.",
            reply_markup=assistant_strategy_keyboard(symbol, timeframe, user_strategies),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "Run /assistant to analyze an asset with your new strategy.",
            reply_markup=cancel_button(),
        )


@router.callback_query(F.data == "asst_manage_strats")
async def cb_manage_strategies(callback: CallbackQuery, session: AsyncSession):
    strategies = await _load_user_strategies(session, callback.from_user.id)
    if not strategies:
        await callback.message.edit_text(
            "⚙️ <b>My Strategies</b>\n\nYou have no custom strategies yet.",
            reply_markup=back_button(),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "⚙️ <b>My Strategies</b>\n\nTap 🗑 Delete to remove a strategy.",
            reply_markup=assistant_manage_keyboard(strategies),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("asst_del:"))
async def cb_delete_strategy(callback: CallbackQuery, session: AsyncSession):
    key = callback.data.split(":", 1)[1]
    user = await db_service.get_user(session, callback.from_user.id)
    if user:
        await delete_user_strategy(session, user.id, key)

    strategies = await get_user_strategies(session, user.id) if user else []
    if strategies:
        await callback.message.edit_text(
            "⚙️ <b>My Strategies</b>\n\nTap 🗑 Delete to remove a strategy.",
            reply_markup=assistant_manage_keyboard(strategies),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            "⚙️ <b>My Strategies</b>\n\nNo custom strategies left.",
            reply_markup=back_button(),
            parse_mode="HTML",
        )
    await callback.answer("Strategy deleted")


@router.callback_query(F.data == "asst_back_strats")
async def cb_back_to_strategies(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    symbol = data.get("asst_symbol")
    timeframe = data.get("asst_timeframe", "1h")

    if not symbol:
        return await cb_back_assets(callback, session)

    user_strategies = await _load_user_strategies(session, callback.from_user.id)
    await callback.message.edit_text(
        f"🧭 <b>Select Strategy for {html.quote(symbol)} ({html.quote(timeframe.upper())})</b>\n\n"
        f"Choose a preset framework or tap ⭐ one of your own strategies.",
        reply_markup=assistant_strategy_keyboard(symbol, timeframe, user_strategies),
        parse_mode="HTML",
    )
    await callback.answer()
