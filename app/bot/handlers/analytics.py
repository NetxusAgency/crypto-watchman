from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.services.prices.price_fetcher import price_fetcher

router = Router(name="analytics_handlers")

@router.message(Command("analytics"))
async def cmd_analytics(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username
    )

    portfolio = await db_service.get_portfolio(session, user.id)

    if not portfolio:
        await message.answer(
            "📭 Your portfolio is empty.\n"
            "Add assets with /add_asset before running analytics.",
            parse_mode="HTML"
        )
        return

    # Fetch current prices for all assets
    prices = {}
    for item in portfolio:
        price = await price_fetcher.get_price(item.symbol)
        if price is not None:
            prices[item.symbol] = price

    if not prices:
        await message.answer("❌ Could not fetch prices for any asset. Try again later.")
        return

    # Calculate PnL for each asset
    results = []
    for item in portfolio:
        current = prices.get(item.symbol)
        if current is None:
            continue
        pct = ((current - item.entry_price) / item.entry_price) * 100
        results.append((item.symbol, item.entry_price, current, pct))

    if not results:
        await message.answer("❌ Could not calculate analytics. Try again later.")
        return

    # Best and worst
    results_sorted = sorted(results, key=lambda r: r[3], reverse=True)
    best = results_sorted[0]
    worst = results_sorted[-1]

    # Build message
    lines = ["📈 <b>Portfolio Analytics</b>\n"]
    arrow_up = "\u2191"
    arrow_down = "\u2193"

    # Best / Worst
    lines.append(f"<b>Best:</b> {arrow_up} {best[0]} <code>{best[3]:+.2f}%</code>")
    lines.append(f"<b>Worst:</b> {arrow_down} {worst[0]} <code>{worst[3]:+.2f}%</code>")
    lines.append(f"<b>Assets Tracked:</b> {len(results)}")
    lines.append(f"<b>Assets With Price:</b> {len(prices)}/{len(portfolio)}")
    lines.append("")

    # Per-asset breakdown
    lines.append("<b>Breakdown:</b>")
    for symbol, entry, current, pct in results_sorted:
        icon = arrow_up if pct >= 0 else arrow_down
        lines.append(f"{icon} <b>{symbol}</b> \u2014 Entry <code>{entry:,.4f}</code> \u2192 <code>{current:,.4f}</code> (<code>{pct:+.2f}%</code>)")

    await message.answer("\n".join(lines), parse_mode="HTML")
