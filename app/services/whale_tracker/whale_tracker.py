import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.models import WhaleTransaction
from app.services.prices.price_fetcher import price_fetcher
from app.services.whale_tracker.assets import get_assets_map, get_erc20_assets
from app.services.whale_tracker.providers import (
    BitcoinWhaleProvider,
    Erc20WhaleProvider,
    EthereumWhaleProvider,
    WhaleProvider,
    WhaleTransfer,
    default_client,
)

logger = logging.getLogger("crypto_watchman.whale_tracker")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dedup_key(asset: str, txid: str) -> str:
    return hashlib.sha256(f"{asset.upper()}:{txid.strip()}".encode()).hexdigest()


class WhaleTracker:
    def __init__(self) -> None:
        self.client = default_client()
        self._last_scan = 0.0
        self._min_scan_interval = 60.0
        self._cache: list[WhaleTransfer] = []

    async def close(self) -> None:
        await self.client.aclose()

    async def _providers(self, session: AsyncSession, symbols: set[str] | None = None) -> list[WhaleProvider]:
        providers: list[WhaleProvider] = []
        if symbols is None or "BTC" in symbols:
            providers.append(BitcoinWhaleProvider(self.client))
        if symbols is None or "ETH" in symbols:
            providers.append(EthereumWhaleProvider(self.client))
        erc20_assets = await get_erc20_assets(session)
        if symbols is not None:
            erc20_assets = [a for a in erc20_assets if a.symbol.upper() in symbols]
        if erc20_assets:
            providers.append(
                Erc20WhaleProvider(
                    self.client,
                    erc20_assets,
                    max_per_asset=settings.WHALE_MAX_ITEMS_PER_ASSET,
                )
            )
        return providers

    async def fetch_all(self, session: AsyncSession, max_items: int = 50, symbols: set[str] | None = None) -> list[WhaleTransfer]:
        """Live multi-asset scan across all providers (no DB writes)."""
        providers = await self._providers(session, symbols)
        per_provider = max(1, max_items + 20)
        results = await asyncio.gather(
            *[p.fetch_whales(per_provider) for p in providers],
            return_exceptions=True,
        )
        out: list[WhaleTransfer] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Whale provider failed: {result}")
                continue
            out.extend(result)
        symbols = {s.upper() for s in symbols} if symbols else None
        # Filter to registered assets at or above their whale threshold.
        assets_map = await get_assets_map(session)
        filtered = [
            t for t in out
            if (symbols is None or t.asset.upper() in symbols)
            and t.asset in assets_map and assets_map[t.asset].whale_threshold > 0
            and t.value >= assets_map[t.asset].whale_threshold
        ]
        filtered.sort(key=lambda t: t.value, reverse=True)
        return filtered[:max_items]

    async def get_whales(self, session: AsyncSession, symbols: set[str] | None = None) -> list[dict]:
        """Live scan formatted for the /whale menu (multi-asset, top 10)."""
        transfers = await self.fetch_all(session, max_items=30, symbols=symbols)
        self._last_scan = time.time()
        return [self._to_dict(t) for t in transfers[:10]]

    async def fetch_and_store(self, session: AsyncSession, symbols: set[str] | None = None) -> list[dict]:
        """Scan, persist new whale txs, and return only the NEW ones (DB-backed dedup)."""
        if time.time() - self._last_scan < self._min_scan_interval and self._cache:
            transfers = self._cache
        else:
            transfers = await self.fetch_all(session, max_items=60, symbols=symbols)
            self._cache = transfers
            self._last_scan = time.time()

        if not transfers:
            return []

        assets_map = await get_assets_map(session)
        usd_tasks = []
        for t in transfers:
            usd_tasks.append(self._usd_value(t.asset, t.value))
        usd_values = await asyncio.gather(*usd_tasks, return_exceptions=True)

        now = _utcnow()
        new_txs: list[dict] = []
        for t, usd in zip(transfers, usd_values):
            dk = dedup_key(t.asset, t.txid)
            exists = await session.execute(select(WhaleTransaction.id).where(WhaleTransaction.dedup_key == dk))
            if exists.scalar_one_or_none():
                continue
            session.add(
                WhaleTransaction(
                    dedup_key=dk,
                    asset=t.asset.upper(),
                    chain=t.chain,
                    value=t.value,
                    value_usd=float(usd) if isinstance(usd, (int, float)) else None,
                    txid=t.txid,
                    from_addr=t.from_addr,
                    to_addr=t.to_addr,
                    source=t.source,
                    direction=t.direction,
                    direction_confidence=t.direction_confidence,
                    created_at=now,
                )
            )
            new_txs.append(self._to_dict(t, value_usd=(float(usd) if isinstance(usd, (int, float)) else None)))

        if new_txs:
            await session.commit()
            logger.info(f"Stored {len(new_txs)} new whale transaction(s)")
        return new_txs

    async def _usd_value(self, symbol: str, amount: float) -> float | None:
        try:
            price = await price_fetcher.get_price(symbol)
            if price:
                return price * amount
        except Exception as e:
            logger.debug(f"USD value fetch failed for {symbol}: {e}")
        return None

    @staticmethod
    def _to_dict(t: WhaleTransfer, value_usd: float | None = None) -> dict:
        return {
            "txid": (t.txid or "")[:12] + "..." if t.txid else "",
            "full_txid": t.txid,
            "asset": t.asset.upper(),
            "chain": t.chain,
            "value": t.value,
            "value_usd": value_usd,
            "source": t.source,
            "to": t.to_addr,
            "from": t.from_addr,
            "direction": t.direction,
            "direction_confidence": t.direction_confidence,
        }


whale_tracker = WhaleTracker()