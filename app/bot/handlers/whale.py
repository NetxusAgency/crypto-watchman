from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services import db_service
from app.services.whale_tracker.whale_tracker import whale_tracker
from app.services.whale_tracker.assets import get_user_scan_symbols

router = Router(name="whale_handlers")


_dir_parts = {
    "BUY": "🟢 BUY",
    "SELL": "🔴 SELL",
    "TRANSFER": "⚪ Transfer",
}


def format_whales(whales: list[dict]) -> list[str]:
    """Format whale rows for Telegram (HTML-safe, shared by /whale + menu)."""
    lines = ["🐋 <b>Recent Whale Transactions</b>\n"]
    for w in whales:
        asset = w["asset"]
        prefix = "₿" if asset == "BTC" else "Ξ" if asset == "ETH" else "🪙"
        direction = _dir_parts.get(w.get("direction", "TRANSFER"), "⚪ Transfer")
        line = f"{direction} {prefix} <b>{asset}</b> {w['value']:,.0f}  | tx: <code>{w['txid']}</code>"
        if w.get("value_usd"):
            line += f" | ≈ <b>${w['value_usd']:,.0f}</b>"
        to_addr = w.get("to", "")
        if to_addr:
            line += f"\n        → <code>{to_addr}</code>"
        lines.append(line)
    lines.append("\n<i>BTC: mempool.space | ETH/ERC-20: Etherscan</i>")
    lines.append("<i>🟢 BUY / 🔴 SELL = DEX or exchange match · ⚪ Transfer = undetermined</i>")
    return lines


@router.message(Command("whale"))
async def cmd_whale(message: Message, session: AsyncSession):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    prefs = await get_user_scan_symbols(session, user.id)
    symbols = set(prefs) if prefs else None

    await message.answer("🐋 Scanning blockchain for large transactions...")

    whales = await whale_tracker.get_whales(session, symbols=symbols)

    if not whales:
        scope = ", ".join(prefs) if prefs else "BTC, ETH, USDT, USDC, LINK, UNI, MATIC, SHIB, PEPE"
        await message.answer(
            f"No large transactions detected right now.\n"
            f"Scanning: <code>{scope}</code>\n"
            "Tap 🔧 Set scan coins to choose which assets to watch.",
            parse_mode="HTML",
        )
        return

    from app.bot.keyboards import whale_menu_keyboard
    scope = ", ".join(prefs) if prefs else "All tracked assets"
    lines = format_whales(whales)
    lines[0] = f"🐋 <b>Recent Whale Transactions</b> — <code>{scope}</code>\n"
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=whale_menu_keyboard())