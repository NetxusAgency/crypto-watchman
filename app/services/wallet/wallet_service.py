import logging

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.models import Wallet, WalletBalance, User
from app.services.wallet.providers import (
    WalletProvider,
    CovalentWalletProvider,
    PublicRpcProvider,
    is_valid_address,
)

logger = logging.getLogger("crypto_watchman.wallet_service")

_wallet_provider: WalletProvider | None = None


def get_wallet_provider() -> WalletProvider:
    global _wallet_provider
    if _wallet_provider is None:
        if settings.COVALENT_API_KEY:
            _wallet_provider = CovalentWalletProvider(settings.COVALENT_API_KEY)
        else:
            _wallet_provider = PublicRpcProvider()
    return _wallet_provider


async def close_wallet_provider() -> None:
    global _wallet_provider
    if _wallet_provider is not None:
        await _wallet_provider.close()
        _wallet_provider = None


def shorten_address(address: str, head: int = 6, tail: int = 4) -> str:
    if len(address) <= head + tail:
        return address
    return f"{address[:head]}…{address[-tail:]}"


async def add_wallet(
    session: AsyncSession,
    user_id: int,
    address: str,
    network: str,
    label: str | None = None,
) -> tuple[Wallet | None, str | None]:
    """Connect a wallet address. Returns (wallet, error_message)."""
    addr = address.strip().lower()
    if not is_valid_address(addr):
        return None, "Invalid address. Must be a valid EVM address like <code>0x…</code> (40 hex chars)."

    existing = (
        await session.execute(
            select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.network == network,
                Wallet.address == addr,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return None, f"Wallet <code>{shorten_address(addr)}</code> is already connected."

    wallet = Wallet(
        user_id=user_id,
        address=addr,
        network=network,
        label=(label or None),
    )
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet, None


async def get_user_wallets(session: AsyncSession, user_id: int) -> list[Wallet]:
    stmt = (
        select(Wallet)
        .where(Wallet.user_id == user_id)
        .options(selectinload(Wallet.balances))
        .order_by(Wallet.id.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def delete_wallet(session: AsyncSession, user_id: int, wallet_id: int) -> bool:
    stmt = delete(Wallet).where(Wallet.user_id == user_id, Wallet.id == wallet_id)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount > 0


async def refresh_wallet(session: AsyncSession, wallet: Wallet) -> None:
    """Fetch latest balances for one wallet and replace stored rows."""
    provider = get_wallet_provider()
    balances = await provider.fetch_balances(wallet.address, wallet.network)

    await session.execute(delete(WalletBalance).where(WalletBalance.wallet_id == wallet.id))
    for tb in balances:
        session.add(
            WalletBalance(
                wallet_id=wallet.id,
                symbol=tb.symbol,
                name=tb.name,
                contract_address=tb.contract_address,
                network=wallet.network,
                quantity=tb.quantity,
                price_usd=tb.price_usd,
                value_usd=tb.value_usd,
            )
        )
    wallet.total_value_usd = round(sum(tb.value_usd or 0 for tb in balances), 2)
    await session.commit()
    await session.refresh(wallet)


async def refresh_all_wallets(session_maker: async_sessionmaker) -> int:
    """Background refresh of every connected wallet. Returns wallets refreshed."""
    refreshed = 0
    async with session_maker() as session:
        result = await session.execute(select(Wallet).order_by(Wallet.id))
        wallets = list(result.scalars().all())
        for wallet in wallets:
            try:
                await refresh_wallet(session, wallet)
                refreshed += 1
            except Exception as e:
                logger.warning(f"Wallet refresh failed for {shorten_address(wallet.address)}: {e}")
    return refreshed


def format_wallet_summary(wallets: list[Wallet]) -> list[str]:
    """Build an HTML-safe wallet overview, one line set per connected wallet."""
    lines = ["👛 <b>Wallet Overview</b>\n"]
    if not wallets:
        lines.append("No wallets connected yet.")
        return lines

    for wallet in wallets:
        balances = sorted(wallet.balances, key=lambda b: b.value_usd, reverse=True)
        name = wallet.label or f"{wallet.network} wallet"
        lines.append(
            f"🧾 <b>{name}</b> — <code>{shorten_address(wallet.address)}</code>\n"
            f"💰 Total ≈ <b>${wallet.total_value_usd:,.2f}</b>\n"
        )
        shown = balances[:10]
        for b in shown:
            qty = f"{b.quantity:,.4f}" if b.quantity < 1000 else f"{b.quantity:,.2f}"
            value = f" ≈ <b>${b.value_usd:,.2f}</b>" if b.value_usd else ""
            lines.append(f"• <b>{b.symbol}</b> — {qty}{value}")
        if len(balances) > 10:
            lines.append(f"<i>… and {len(balances) - 10} more assets</i>")
        lines.append("")
    lines.append("<i>Updated by on-chain balance snapshot (public address only, no keys stored).</i>")
    return lines