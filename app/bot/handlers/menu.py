from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.services.prices.price_fetcher import price_fetcher
from app.services.sentiment.sentiment_monitor import sentiment_monitor
from app.services.whale_tracker.whale_tracker import whale_tracker
from app.services.market_digest.digest_service import generate_digest
from app.bot.keyboards import (
    main_menu_keyboard, portfolio_actions_keyboard, alerts_actions_keyboard,
    back_button, cancel_button,
)
from app.bot.states import PortfolioStates, AlertStates

router = Router(name="menu_handlers")


@router.message(F.text == "📊 Portfolio")
async def menu_portfolio(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await message.answer(
            "📭 Your portfolio is empty.\nTap ➕ Add Asset to start tracking.",
            reply_markup=portfolio_actions_keyboard(),
        )
        return

    lines = ["📊 <b>Your Portfolio</b>\n"]
    for item in portfolio:
        price = await price_fetcher.get_price(item.symbol)
        if price:
            pct = ((price - item.entry_price) / item.entry_price) * 100
            arrow = "🟢" if pct >= 0 else "🔴"
            lines.append(
                f"{arrow} <b>{item.symbol}</b> — Entry <code>{item.entry_price:,.4f}</code> "
                f"→ <code>{price:,.4f}</code> (<code>{pct:+.2f}%</code>)"
            )
        else:
            lines.append(f"❓ <b>{item.symbol}</b> — Entry <code>{item.entry_price:,.4f}</code> (price unavailable)")

    if user.plan == "free":
        lines.append(f"\nAssets: {len(portfolio)}/5")
    else:
        lines.append(f"\nAssets: {len(portfolio)} (Unlimited)")

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=portfolio_actions_keyboard())


@router.message(F.text == "🔔 Alerts")
async def menu_alerts(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    alerts = await db_service.get_user_alerts(session, user.id)
    if not alerts:
        await message.answer(
            "🔔 No active alerts.\nTap ➕ Add Alert to set one up.",
            reply_markup=alerts_actions_keyboard(),
        )
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

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=alerts_actions_keyboard())


@router.message(F.text == "📈 Analytics")
async def menu_analytics(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await message.answer("📭 Portfolio is empty.", reply_markup=back_button())
        return

    prices = {}
    for item in portfolio:
        price = await price_fetcher.get_price(item.symbol)
        if price is not None:
            prices[item.symbol] = price

    if not prices:
        await message.answer("❌ Could not fetch prices.", reply_markup=back_button())
        return

    results = []
    for item in portfolio:
        current = prices.get(item.symbol)
        if current is None:
            continue
        pct = ((current - item.entry_price) / item.entry_price) * 100
        results.append((item.symbol, item.entry_price, current, pct))

    results.sort(key=lambda r: r[3], reverse=True)
    best = results[0]
    worst = results[-1]

    lines = ["📈 <b>Portfolio Analytics</b>\n"]
    lines.append(f"🟢 Best: <b>{best[0]}</b> <code>{best[3]:+.2f}%</code>")
    lines.append(f"🔴 Worst: <b>{worst[0]}</b> <code>{worst[3]:+.2f}%</code>")
    lines.append(f"📊 Assets with price: {len(prices)}/{len(portfolio)}\n")
    lines.append("<b>Breakdown:</b>")
    for sym, entry, current, pct in results:
        icon = "🟢" if pct >= 0 else "🔴"
        lines.append(f"{icon} <b>{sym}</b> Entry <code>{entry:,.4f}</code> → <code>{current:,.4f}</code> (<code>{pct:+.2f}%</code>)")

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=back_button())


@router.message(F.text == "📢 Sentiment")
async def menu_sentiment(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    if not portfolio:
        await message.answer("📭 Portfolio is empty.", reply_markup=back_button())
        return

    await message.answer("🔍 Scanning Reddit and news...")
    results = []
    for item in portfolio:
        mention = await sentiment_monitor.get_mention_count(item.symbol)
        results.append(mention)

    lines = ["📢 <b>Social Sentiment</b>\n"]
    for r in results:
        icon = "🔥" if r["total"] >= 10 else "📈" if r["total"] >= 3 else "💤"
        lines.append(f"{icon} <b>{r['symbol']}</b> — Reddit: {r['reddit']} | News: {r['news']} | Total: <b>{r['total']}</b>")
    lines.append("\n<i>r/cryptocurrency + crypto news (24h).</i>")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=back_button())


@router.message(F.text == "🐋 Whales")
async def menu_whale(message: Message):
    await message.answer("🐋 Scanning blockchain...")
    whales = await whale_tracker.get_whales()
    if not whales:
        await message.answer(
            "No large transactions detected.\nThresholds: ≥10 BTC, ≥100 ETH",
            reply_markup=back_button(),
        )
        return
    lines = ["🐋 <b>Recent Whale Transactions</b>\n"]
    for w in whales:
        prefix = "₿" if w["asset"] == "BTC" else "Ξ"
        line = f"{prefix} <b>{w['asset']}</b> {w['value']:,.2f}  | tx: <code>{w['txid']}</code>"
        to_addr = w.get("to", "")
        if to_addr:
            line += f" | to: <code>{to_addr}</code>"
        lines.append(line)
    lines.append("\n<i>BTC: mempool.space | ETH: Etherscan</i>")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=back_button())


@router.message(F.text == "🧠 Digest")
async def menu_digest(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    await message.answer("🧠 Generating market digest...")
    digest = await generate_digest(session, user.id)
    await message.answer(digest, parse_mode="HTML", reply_markup=back_button())


@router.message(F.text == "⚙️ Settings")
async def menu_settings(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    alerts = await db_service.get_user_alerts(session, user.id)
    if user.plan == "free":
        limit = f"{len(portfolio)} / 5 (Free)"
        upgrade = "\n<i>Pro: unlimited assets + listing alerts</i>"
    else:
        limit = f"{len(portfolio)} (Unlimited)"
        upgrade = "\n<i>✅ Pro features active</i>"
    await message.answer(
        f"⚙️ <b>Settings</b>\n\n"
        f"ID: <code>{user.telegram_id}</code>\n"
        f"Plan: <b>{user.plan.upper()}</b>\n"
        f"Assets: {limit}\n"
        f"Alerts: {len(alerts)}{upgrade}",
        parse_mode="HTML",
        reply_markup=back_button(),
    )


@router.message(F.text == "❓ Help")
async def menu_help(message: Message):
    await message.answer(
        "🤖 <b>Crypto & Forex Watchman</b>\n\n"
        "Tap the menu buttons below to navigate.\n\n"
        "<b>Alert types:</b>\n"
        "• <b>Above/Below</b> — price threshold\n"
        "• <b>Move %</b> — ±X% from your entry\n"
        "• <b>Volume</b> — 24h volume exceeds $X\n"
        "• <b>Volatility</b> — daily move > Xx normal\n"
        "• <b>Sentiment</b> — Reddit+news mentions > X\n"
        "• <b>Whale</b> — on-chain tx ≥ X units\n\n"
        "Commands still work too: /help, /portfolio, etc.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.message(StateFilter("*"), F.text.in_({"📊 Portfolio", "🔔 Alerts", "📈 Analytics", "📢 Sentiment", "🐋 Whales", "🧠 Digest", "⚙️ Settings", "❓ Help"}))
async def fsm_cancel_to_menu(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    text = message.text
    if text == "📊 Portfolio":
        await menu_portfolio(message, session)
    elif text == "🔔 Alerts":
        await menu_alerts(message, session)
    elif text == "📈 Analytics":
        await menu_analytics(message, session)
    elif text == "📢 Sentiment":
        await menu_sentiment(message, session)
    elif text == "🐋 Whales":
        await menu_whale(message)
    elif text == "🧠 Digest":
        await menu_digest(message, session)
    elif text == "⚙️ Settings":
        await menu_settings(message, session)
    elif text == "❓ Help":
        await menu_help(message)


@router.message(PortfolioStates.waiting_for_asset)
async def fsm_add_asset(message: Message, session: AsyncSession, state: FSMContext):
    args = message.text.strip().split()
    if len(args) < 2:
        await message.answer(
            "⚠️ Format: <code>SYMBOL PRICE</code>\nExample: <code>BTC 62900</code>",
            parse_mode="HTML",
            reply_markup=portfolio_actions_keyboard(),
        )
        return
    symbol = args[0].upper().strip()
    try:
        entry_price = float(args[1].replace(",", ""))
        if entry_price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Price must be a valid positive number.", reply_markup=portfolio_actions_keyboard())
        return

    await state.clear()
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    is_existing = any(item.symbol == symbol for item in portfolio)
    if len(portfolio) >= 5 and not is_existing and user.plan == "free":
        await message.answer("⚠️ Free tier limit: 5 assets.", reply_markup=portfolio_actions_keyboard())
        return

    asset = await db_service.add_portfolio_asset(session, user.id, symbol, entry_price)
    await message.answer(
        f"✅ <b>{asset.symbol}</b> added at <code>{asset.entry_price:,.4f}</code>",
        parse_mode="HTML",
        reply_markup=portfolio_actions_keyboard(),
    )


@router.message(AlertStates.waiting_target)
async def fsm_add_alert(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    symbol = data.get("alert_symbol")
    alert_type = data.get("alert_type")
    await state.clear()

    try:
        target_value = float(message.text.strip().replace(",", ""))
        if target_value <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Target must be a valid positive number.",
            reply_markup=alerts_actions_keyboard(),
        )
        return

    direction = alert_type
    if direction == "above":
        alert_db_type = "price_above"
        desc = f"Price above <code>{target_value:,.4f}</code>"
    elif direction == "below":
        alert_db_type = "price_below"
        desc = f"Price below <code>{target_value:,.4f}</code>"
    elif direction == "move":
        alert_db_type = "move_percent"
        desc = f"±{target_value}% from entry"
    elif direction == "volume":
        alert_db_type = "volume_above"
        desc = f"Volume > ${target_value:,.0f}"
    elif direction == "volatility":
        alert_db_type = "volatility"
        desc = f"Volatility > {target_value}x normal"
    elif direction == "sentiment":
        alert_db_type = "sentiment_spike"
        desc = f"Mentions > {target_value:.0f}/24h"
    elif direction == "whale":
        alert_db_type = "whale_alert"
        desc = f"Tx ≥ {target_value:.0f} {symbol}"
    else:
        await message.answer("❌ Invalid alert type.", reply_markup=alerts_actions_keyboard())
        return

    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    portfolio = await db_service.get_portfolio(session, user.id)
    has_asset = any(item.symbol == symbol for item in portfolio)
    if not has_asset:
        await message.answer(
            f"⚠️ <b>{symbol}</b> not in portfolio. Add it first.",
            parse_mode="HTML",
            reply_markup=alerts_actions_keyboard(),
        )
        return

    alert = await db_service.add_alert(session, user.id, symbol, alert_db_type, target_value)
    await message.answer(
        f"🔔 <b>Alert Set!</b>\nID: <code>{alert.id}</code> | <b>{symbol}</b> — {desc}",
        parse_mode="HTML",
        reply_markup=alerts_actions_keyboard(),
    )
