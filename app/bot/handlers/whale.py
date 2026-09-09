from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.services.whale_tracker.whale_tracker import whale_tracker

router = Router(name="whale_handlers")

@router.message(Command("whale"))
async def cmd_whale(message: Message):
    await message.answer("🐋 Scanning blockchain for large transactions...")

    whales = await whale_tracker.get_whales()

    if not whales:
        await message.answer(
            "No large transactions detected right now.\n"
            "Thresholds: ≥10 BTC, ≥100 ETH",
            parse_mode="HTML"
        )
        return

    lines = ["🐋 <b>Recent Whale Transactions</b>\n"]
    for w in whales:
        asset = w["asset"]
        value = w["value"]
        txid = w["txid"]
        to_addr = w.get("to", "")
        prefix = "₿" if asset == "BTC" else "Ξ"
        line = f"{prefix} <b>{asset}</b> {value:,.2f}  | tx: <code>{txid}</code>"
        if to_addr:
            line += f" | to: <code>{to_addr}</code>"
        lines.append(line)

    lines.append("\n<i>BTC from mempool.space, ETH from Etherscan.</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")
