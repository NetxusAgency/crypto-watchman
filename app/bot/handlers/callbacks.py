from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.bot.keyboards import (
    main_menu_keyboard, portfolio_actions_keyboard, asset_list_keyboard,
    confirm_remove_keyboard, alerts_actions_keyboard, alert_types_keyboard,
    alert_asset_list_keyboard, alert_list_keyboard, confirm_alert_remove_keyboard,
)
from app.bot.states import PortfolioStates, AlertStates

router = Router(name="callback_handlers")


@router.callback_query(F.data == "menu_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Main Menu</b>\n\nChoose an option below:",
        parse_mode="HTML",
    )
    await callback.message.answer(
        "Tap a button:",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu_portfolio")
async def back_to_portfolio(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await callback.message.edit_text(
            "📭 Portfolio is empty.",
            reply_markup=portfolio_actions_keyboard(),
        )
        await callback.answer()
        return
    from app.services.prices.price_fetcher import price_fetcher
    lines = ["📊 <b>Your Portfolio</b>\n"]
    for item in portfolio:
        price = await price_fetcher.get_price(item.symbol)
        if price:
            pct = ((price - item.entry_price) / item.entry_price) * 100
            arrow = "🟢" if pct >= 0 else "🔴"
            lines.append(f"{arrow} <b>{item.symbol}</b> — Entry <code>{item.entry_price:,.4f}</code> → <code>{price:,.4f}</code> (<code>{pct:+.2f}%</code>)")
        else:
            lines.append(f"❓ <b>{item.symbol}</b> — Entry <code>{item.entry_price:,.4f}</code>")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=portfolio_actions_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu_alerts")
async def back_to_alerts(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    alerts = await db_service.get_user_alerts(session, user.id)
    if not alerts:
        await callback.message.edit_text(
            "🔔 No active alerts.",
            reply_markup=alerts_actions_keyboard(),
        )
        await callback.answer()
        return
    lines = ["🔔 <b>Your Active Alerts</b>\n"]
    for a in alerts:
        if a.alert_type == "move_percent":
            desc = f"±{a.target_value}% from entry"
        elif a.alert_type == "volume_above":
            desc = f"Volume > ${a.target_value:,.0f}"
        elif a.alert_type == "volatility":
            desc = f"Volatility > {a.target_value}x"
        elif a.alert_type == "sentiment_spike":
            desc = f"Mentions > {a.target_value:.0f}/24h"
        elif a.alert_type == "whale_alert":
            desc = f"Tx ≥ {a.target_value:.0f} {a.symbol}"
        else:
            desc = f"{a.alert_type.replace('price_', '').title()} {a.target_value:,.4f}"
        lines.append(f"• ID {a.id} | <b>{a.symbol}</b> — {desc}")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=alerts_actions_keyboard())
    await callback.answer()


@router.callback_query(F.data == "portfolio_add")
async def portfolio_add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PortfolioStates.waiting_for_asset)
    await callback.message.edit_text(
        "➕ <b>Add Asset</b>\n\n"
        "Send me the symbol and entry price.\n"
        "Example: <code>BTC 62900</code> or <code>EURUSD 1.08</code>\n\n"
        "Or tap ❌ Cancel below.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "portfolio_remove_list")
async def portfolio_remove_list(callback: CallbackQuery, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await callback.message.edit_text("📭 No assets to remove.", reply_markup=portfolio_actions_keyboard())
        await callback.answer()
        return
    symbols = [item.symbol for item in portfolio]
    await callback.message.edit_text(
        "✖ <b>Remove Asset</b>\n\nTap an asset to remove:",
        parse_mode="HTML",
        reply_markup=asset_list_keyboard(symbols),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("portfolio_remove:"))
async def portfolio_remove_confirm(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"Remove <b>{symbol}</b> and all its alerts?",
        parse_mode="HTML",
        reply_markup=confirm_remove_keyboard(symbol),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("portfolio_remove_confirm:"))
async def portfolio_remove_execute(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    symbol = callback.data.split(":")[1]
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    success = await db_service.remove_portfolio_asset(session, user.id, symbol)
    if success:
        text = f"✅ <b>{symbol}</b> and its alerts removed."
    else:
        text = f"❌ Could not remove <b>{symbol}</b>."
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=portfolio_actions_keyboard())
    await callback.answer()


@router.callback_query(F.data == "alert_add_choose_asset")
async def alert_add_choose_asset(callback: CallbackQuery, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await callback.message.edit_text(
            "📭 No assets in portfolio. Add one first.",
            reply_markup=alerts_actions_keyboard(),
        )
        await callback.answer()
        return
    symbols = [item.symbol for item in portfolio]
    await callback.message.edit_text(
        "🔔 <b>Add Alert</b>\n\nChoose an asset:",
        parse_mode="HTML",
        reply_markup=alert_asset_list_keyboard(symbols),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_add_type:"))
async def alert_add_choose_type(callback: CallbackQuery):
    symbol = callback.data.split(":")[1]
    await callback.message.edit_text(
        f"🔔 <b>Add Alert for {symbol}</b>\n\nChoose alert type:",
        parse_mode="HTML",
        reply_markup=alert_types_keyboard(symbol),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_type:"))
async def alert_add_ask_target(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    symbol = parts[1]
    alert_type = parts[2]
    await state.set_state(AlertStates.waiting_target)
    await state.update_data(alert_symbol=symbol, alert_type=alert_type)
    prompts = {
        "above": f"Send the target price for {symbol}\nExample: <code>70000</code>",
        "below": f"Send the target price for {symbol}\nExample: <code>60000</code>",
        "move": f"Send the % move threshold for {symbol}\nExample: <code>5</code> (±5% from entry)",
        "volume": f"Send the 24h volume threshold (USD)\nExample: <code>50000000000</code>",
        "volatility": f"Send the volatility multiplier\nExample: <code>2</code> (2x normal daily move)",
        "sentiment": f"Send the mention threshold\nExample: <code>10</code> (alert when 24h mentions exceed this)",
        "whale": f"Send the minimum transaction value\nExample: <code>10</code> (alert on tx ≥ 10 {symbol})",
    }
    prompt = prompts.get(alert_type, f"Enter target value for {symbol}")
    await callback.message.edit_text(
        f"🔔 <b>Set Alert</b>\n\n{prompt}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "alert_remove_list")
async def alert_remove_list(callback: CallbackQuery, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    alerts = await db_service.get_user_alerts(session, user.id)
    if not alerts:
        await callback.message.edit_text("🔔 No active alerts.", reply_markup=alerts_actions_keyboard())
        await callback.answer()
        return
    await callback.message.edit_text(
        "✖ <b>Remove Alert</b>\n\nTap an alert to remove:",
        parse_mode="HTML",
        reply_markup=alert_list_keyboard(alerts),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_remove:"))
async def alert_remove_confirm(callback: CallbackQuery):
    alert_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        f"Remove alert ID <b>{alert_id}</b>?",
        parse_mode="HTML",
        reply_markup=confirm_alert_remove_keyboard(alert_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert_remove_confirm:"))
async def alert_remove_execute(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    alert_id = int(callback.data.split(":")[1])
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    success = await db_service.remove_alert_by_id(session, user.id, alert_id)
    if success:
        text = f"✅ Alert <b>{alert_id}</b> removed."
    else:
        text = f"❌ Could not remove alert <b>{alert_id}</b>."
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=alerts_actions_keyboard())
    await callback.answer()
