from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.whale_tracker.whale_tracker import whale_tracker

router = Router(name="whale_handlers")


@router.message(Command("whale"))
async def cmd_whale(message: Message, session: AsyncSession):
    await message.answer("🐋 Scanning blockchain for large transactions...")

    whales = await whale_tracker.get_whales(session)

    if not whales:
        await message.answer(
            "No large transactions detected right now.\n"
            "Covers: BTC (≥10) · ETH (≥100) · USDT/USDC (≥1M) · LINK/UNI/others (per asset)",
            parse_mode="HTML",
        )
        return

    lines = ["🐋 <b>Recent Whale Transactions</b>\n"]
    for w in whales:
        asset = w["asset"]
        value = w["value"]
        txid = w["txid"]
        to_addr = w.get("to", "")
        prefix = "₿" if asset == "BTC" else "Ξ" if asset == "ETH" else "🪙"
        line = f"{prefix} <b>{asset}</b> {value:,.0f}  | tx: <code>{txid}</code>"
        if w.get("value_usd"):
            line += f" | ≈ <b>${w['value_usd']:,.0f}</b>"
        if to_addr:
            line += f" | to: <code>{to_addr}</code>"
        lines.append(line)

    lines.append("\n<i>BTC: mempool.space | ETH/ERC-20: Etherscan</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")