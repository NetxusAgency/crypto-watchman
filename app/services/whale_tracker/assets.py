from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Asset

# Default on-chain assets tracked for whale monitoring.
# `whale_threshold` is the default min value (native units), overridable per DB row.
DEFAULT_ASSETS: list[dict] = [
    {"symbol": "BTC", "name": "Bitcoin", "chain": "bitcoin", "contract_address": None, "decimals": 8,
     "whale_threshold": 10.0, "tier": "high", "flags": "listed,king"},
    {"symbol": "ETH", "name": "Ethereum", "chain": "ethereum", "contract_address": None, "decimals": 18,
     "whale_threshold": 100.0, "tier": "high", "flags": "listed,smart-contracts"},
    {"symbol": "USDT", "name": "Tether", "chain": "ethereum",
     "contract_address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6,
     "whale_threshold": 1_000_000.0, "tier": "high", "flags": "stablecoin"},
    {"symbol": "USDC", "name": "USD Coin", "chain": "ethereum",
     "contract_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6,
     "whale_threshold": 1_000_000.0, "tier": "high", "flags": "stablecoin"},
    {"symbol": "LINK", "name": "Chainlink", "chain": "ethereum",
     "contract_address": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "decimals": 18,
     "whale_threshold": 100_000.0, "tier": "medium", "flags": "listed,oracle"},
    {"symbol": "UNI", "name": "Uniswap", "chain": "ethereum",
     "contract_address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "decimals": 18,
     "whale_threshold": 100_000.0, "tier": "medium", "flags": "listed,dex"},
    {"symbol": "MATIC", "name": "Polygon", "chain": "ethereum",
     "contract_address": "0x7D1AfA7B718fB893dB30A3aBc0Cfc608AaCfeBB0", "decimals": 18,
     "whale_threshold": 1_000_000.0, "tier": "medium", "flags": "listed,l2"},
    {"symbol": "SHIB", "name": "Shiba Inu", "chain": "ethereum",
     "contract_address": "0x95aD61b0a150d79219dCF64E1E6cc01f0B64C4cE", "decimals": 18,
     "whale_threshold": 1_000_000_000_000.0, "tier": "low", "flags": "meme"},
{"symbol": "PEPE", "name": "Pepe", "chain": "ethereum",
     "contract_address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "decimals": 18,
     "whale_threshold": 1_000_000_000_000.0, "tier": "low", "flags": "meme"},
]


async def seed_assets(session: AsyncSession) -> int:
    """Insert default asset registry rows that don't exist yet. Returns rows added."""
    created = 0
    for row in DEFAULT_ASSETS:
        exists = await session.execute(select(Asset.id).where(Asset.symbol == row["symbol"]))
        if exists.scalar_one_or_none():
            continue
        session.add(Asset(**row))
        created += 1
    if created:
        await session.commit()
    return created


async def get_assets_map(session: AsyncSession) -> dict[str, Asset]:
    rows = (await session.execute(select(Asset))).scalars().all()
    return {a.symbol.upper(): a for a in rows}


async def get_erc20_assets(session: AsyncSession) -> list[Asset]:
    """Ethereum ERC-20 assets with a contract address (skip native ETH/BTC)."""
    rows = (await session.execute(
        select(Asset).where(Asset.chain == "ethereum", Asset.contract_address.is_not(None))
    )).scalars().all()
    return [a for a in rows if a.contract_address]