from aiogram import Router, F, html
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards import (
    cancel_button,
    wallet_menu_keyboard,
    wallet_remove_confirm_keyboard,
    wallet_remove_list_keyboard,
)
from app.bot.states import WalletStates
from app.services import db_service
from app.services.wallet.providers import normalize_network, SUPPORTED_NETWORKS
from app.services.wallet.wallet_service import (
    add_wallet,
    delete_wallet,
    format_wallet_summary,
    get_user_wallets,
    refresh_wallet,
    shorten_address,
)

router = Router(name="wallet_handlers")


async def wallet_panel(session: AsyncSession, telegram_id: int):
    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=telegram_id,
    )
    wallets = await get_user_wallets(session, user.id)
    if wallets:
        text = "\n".join(format_wallet_summary(wallets))
    else:
        text = (
            "👛 <b>Wallet</b>\n\n"
            "No wallets connected yet.\n\n"
            "Connect a <b>public EVM address</b> to see its balances — "
            "Ethereum, Polygon, BSC, Arbitrum, Optimism, Base or Avalanche.\n\n"
            "<i>Only public addresses are stored; private keys are never required.</i>"
        )
    return text, wallet_menu_keyboard()


@router.message(Command("wallet"))
async def cmd_wallet(message: Message, session: AsyncSession):
    text, keyboard = await wallet_panel(session, message.from_user.id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "wallet_back")
async def cb_wallet_back(callback: CallbackQuery, session: AsyncSession):
    text, keyboard = await wallet_panel(session, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "wallet_add")
async def cb_wallet_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WalletStates.waiting_for_address)
    await callback.message.edit_text(
        "➕ <b>Connect a Wallet</b>\n\n"
        "Send a public EVM address.\n"
        "You can optionally add a network and label:\n\n"
        "<code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n"
        "<code>0x… polygon My Savings</code>\n\n"
        f"Networks: <code>{', '.join(sorted(SUPPORTED_NETWORKS))}</code>",
        reply_markup=cancel_button(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(WalletStates.waiting_for_address)
async def process_wallet_address(message: Message, session: AsyncSession, state: FSMContext):
    parts = (message.text or "").strip().split(maxsplit=2)
    address = (parts[0] if parts else "").strip()
    try:
        network = normalize_network(parts[1]) if len(parts) > 1 else "ethereum"
    except ValueError as e:
        await message.answer(
            f"❌ {str(e)}",
            reply_markup=cancel_button(),
            parse_mode="HTML",
        )
        return
    label = parts[2] if len(parts) > 2 else None

    user = await db_service.get_or_create_user(
        session=session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    wallet, error = await add_wallet(session, user.id, address, network, label)
    if error:
        await message.answer(error, reply_markup=cancel_button(), parse_mode="HTML")
        return

    await message.answer(
        f"🔄 Fetching balances for <code>{shorten_address(wallet.address)}</code> ({wallet.network})…",
        parse_mode="HTML",
    )
    try:
        await refresh_wallet(session, wallet)
        await state.clear()
        text, keyboard = await wallet_panel(session, message.from_user.id)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        await delete_wallet(session, user.id, wallet.id)
        await state.clear()
        await message.answer(
            f"❌ Could not load balances for that wallet.\n<code>{html.quote(str(e))}</code>",
            reply_markup=cancel_button(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "wallet_list")
async def cb_wallet_list(callback: CallbackQuery, session: AsyncSession):
    user = await db_service.get_user(session, callback.from_user.id)
    wallets = await get_user_wallets(session, user.id) if user else []
    if not wallets:
        await cb_wallet_back(callback, session)
        return
    lines = ["✖ <b>Which wallet to remove?</b>\n"]
    for w in wallets:
        lines.append(f"• <code>{shorten_address(w.address)}</code> ({w.network})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=wallet_remove_list_keyboard(wallets),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("wallet_remove:"))
async def cb_wallet_remove_confirm(callback: CallbackQuery):
    wallet_id = int(callback.data.split(":", 1)[1])
    await callback.message.edit_text(
        "⚠️ Remove this wallet and its stored balances?",
        reply_markup=wallet_remove_confirm_keyboard(wallet_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("wallet_del:"))
async def cb_wallet_delete(callback: CallbackQuery, session: AsyncSession):
    wallet_id = int(callback.data.split(":", 1)[1])
    user = await db_service.get_user(session, callback.from_user.id)
    if user:
        await delete_wallet(session, user.id, wallet_id)
    text, keyboard = await wallet_panel(session, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Wallet removed")


@router.callback_query(F.data == "wallet_refresh")
async def cb_wallet_refresh(callback: CallbackQuery, session: AsyncSession):
    await callback.message.edit_text("🔄 <i>Refreshing wallet balances…</i>", parse_mode="HTML")
    await callback.answer()
    user = await db_service.get_user(session, callback.from_user.id)
    wallets = await get_user_wallets(session, user.id) if user else []
    for wallet in wallets:
        try:
            await refresh_wallet(session, wallet)
        except Exception as e:
            user_msg = ""
            text, keyboard = await wallet_panel(session, callback.from_user.id)
            await callback.message.edit_text(
                text + f"\n\n⚠️ Refresh partially failed: <code>{html.quote(str(e))}</code>",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return
    text, keyboard = await wallet_panel(session, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")