import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from app.core.config import settings
from app.services.whale_tracker.assets import Asset
from app.services.whale_tracker.direction import classify_direction

logger = logging.getLogger("crypto_watchman.whale_providers")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

MEMPOOL_API = "https://mempool.space/api"
ETHERSCAN_API = "https://api.etherscan.io/api"


def _extract_btc_from_to(tx: dict) -> tuple[str | None, str | None]:
    """Extract a representative from/to address from a mempool/blockstream tx."""
    from_addr = None
    for vin in tx.get("vin", []) or []:
        prevout = vin.get("prevout") or {}
        addr = prevout.get("scriptpubkey_address")
        if addr:
            from_addr = addr
            break
    to_addr = None
    for vout in tx.get("vout", []) or []:
        addr = vout.get("scriptpubkey_address")
        if addr:
            to_addr = addr
            break
    return from_addr, to_addr


@dataclass
class WhaleTransfer:
    asset: str
    chain: str
    value: float
    txid: str
    from_addr: str | None = None
    to_addr: str | None = None
    source: str = "unknown"
    detected_at: datetime | None = None
    direction: str = "TRANSFER"
    direction_confidence: str = "low"
    raw: dict = field(default_factory=dict)


class WhaleProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def fetch_whales(self, max_items: int) -> list[WhaleTransfer]:
        raise NotImplementedError


class BitcoinWhaleProvider(WhaleProvider):
    """Large BTC transactions from the latest mempool.space block."""
    name = "mempool.space"
    chain = "bitcoin"
    asset = "BTC"

    def __init__(self, client: httpx.AsyncClient, decimal_places: int = 8) -> None:
        self.client = client
        self.decimal_places = decimal_places

    async def fetch_whales(self, max_items: int) -> list[WhaleTransfer]:
        result = await self._fetch_mempool(max_items)
        if result:
            return result
        # Fallback source (some regions/ISPs block mempool.space).
        result = await self._fetch_blockstream(max_items)
        if result:
            return result
        return []

    async def _fetch_mempool(self, max_items: int) -> list[WhaleTransfer]:
        try:
            resp = await self.client.get(f"{MEMPOOL_API}/v1/blockchain")
            resp.raise_for_status()
            block_hash = resp.json().get("tipHashKey", "")
        except Exception as e:
            logger.warning(f"mempool.space heartbeat failed: {e}")
            return []

        if not block_hash:
            return []

        out: list[WhaleTransfer] = []
        for offset in (0, 25):
            try:
                block_resp = await self.client.get(
                    f"{MEMPOOL_API}/block/{block_hash}/txs", params={"offset": offset}
                )
                block_resp.raise_for_status()
            except Exception as e:
                logger.warning(f"mempool.space block txs failed: {e}")
                break
            for tx in block_resp.json() or []:
                total_sats = sum(o.get("value", 0) for o in tx.get("vout", []))
                btc = total_sats / 10 ** self.decimal_places
                if btc > 0:
                    from_addr, to_addr = _extract_btc_from_to(tx)
                    direction, confidence = classify_direction(self.chain, from_addr, to_addr)
                    out.append(
                        WhaleTransfer(
                            asset=self.asset,
                            chain=self.chain,
                            value=btc,
                            txid=tx.get("txid", ""),
                            from_addr=from_addr,
                            to_addr=to_addr,
                            source=self.name,
                            direction=direction,
                            direction_confidence=confidence,
                            raw=tx,
                        )
                    )
            if len(out) >= max_items:
                break
        return out[:max_items]

    async def _fetch_blockstream(self, max_items: int) -> list[WhaleTransfer]:
        try:
            resp = await self.client.get("https://blockstream.info/api/blocks/tip/hash")
            resp.raise_for_status()
            block_hash = resp.text.strip()
        except Exception as e:
            logger.warning(f"Blockstream esplora tip failed: {e}")
            return []

        if not block_hash:
            return []

        out: list[WhaleTransfer] = []
        try:
            block_resp = await self.client.get(f"https://blockstream.info/api/block/{block_hash}/txs")
            block_resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Blockstream esplora txs failed: {e}")
            return []

        for tx in block_resp.json() or []:
            total_sats = sum(o.get("value", 0) for o in tx.get("vout", []))
            btc = total_sats / 10 ** self.decimal_places
            if btc > 0:
                from_addr, to_addr = _extract_btc_from_to(tx)
                direction, confidence = classify_direction(self.chain, from_addr, to_addr)
                out.append(
                    WhaleTransfer(
                        asset=self.asset,
                        chain=self.chain,
                        value=btc,
                        txid=tx.get("txid", ""),
                        from_addr=from_addr,
                        to_addr=to_addr,
                        source="Blockstream",
                        direction=direction,
                        direction_confidence=confidence,
                        raw=tx,
                    )
                )
        return out[:max_items]


class EthereumWhaleProvider(WhaleProvider):
    """Large native ETH transfers from the latest block (Etherscan proxy) - requires ETHERSCAN_API_KEY."""
    name = "Etherscan"
    chain = "ethereum"
    asset = "ETH"

    def __init__(self, client: httpx.AsyncClient, decimal_places: int = 18, fallback_amount: float = 100.0) -> None:
        self.client = client
        self.decimal_places = decimal_places
        self.fallback_amount = fallback_amount

    async def fetch_whales(self, max_items: int) -> list[WhaleTransfer]:
        if not settings.ETHERSCAN_API_KEY:
            return []
        try:
            url = (
                f"{ETHERSCAN_API}"
                f"?module=proxy&action=eth_getBlockByNumber&tag=latest&boolean=true"
                f"&apikey={settings.ETHERSCAN_API_KEY}"
            )
            resp = await self.client.get(url)
            resp.raise_for_status()
            txs = (resp.json().get("result") or {}).get("transactions", [])
        except Exception as e:
            logger.warning(f"Etherscan ETH block fetch failed: {e}")
            return []

        out: list[WhaleTransfer] = []
        for tx in txs[:200]:
            try:
                value_wei = int(tx.get("value", "0x0"), 16)
            except (ValueError, TypeError):
                continue
            eth = value_wei / 10 ** self.decimal_places
            direction, confidence = classify_direction(self.chain, tx.get("from"), tx.get("to"))
            out.append(
                WhaleTransfer(
                    asset=self.asset,
                    chain=self.chain,
                    value=eth,
                    txid=tx.get("hash", ""),
                    from_addr=tx.get("from"),
                    to_addr=tx.get("to"),
                    source=self.name,
                    direction=direction,
                    direction_confidence=confidence,
                    raw=tx,
                )
            )
        return out[:max_items]


class Erc20WhaleProvider(WhaleProvider):
    """Large ERC-20 transfers on Ethereum for registered assets (Etherscan tokentx).

    Requires ETHERSCAN_API_KEY. Scans the latest transfers per token contract and
    returns values already converted to human units using each asset's decimals.
    """
    name = "Etherscan"
    chain = "ethereum"

    def __init__(self, client: httpx.AsyncClient, assets: list[Asset], max_per_asset: int = 50) -> None:
        self.client = client
        self.assets = [a for a in assets if a.contract_address]
        self.max_per_asset = max_per_asset

    async def fetch_whales(self, max_items: int) -> list[WhaleTransfer]:
        if not settings.ETHERSCAN_API_KEY:
            return []
        tasks = [self._fetch_token(a.contract_address, a.symbol.upper(), a.decimals) for a in self.assets]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[WhaleTransfer] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            out.extend(result)
        out.sort(key=lambda t: t.value, reverse=True)
        return out[:max_items]

    async def _fetch_token(self, contract: str, symbol: str, decimals: int) -> list[WhaleTransfer]:
        try:
            url = (
                f"{ETHERSCAN_API}?module=account&action=tokentx"
                f"&contractaddress={contract}&page=1&offset={self.max_per_asset}&sort=desc"
                f"&apikey={settings.ETHERSCAN_API_KEY}"
            )
            resp = await self.client.get(url)
            resp.raise_for_status()
            result = resp.json().get("result") or []
            if not isinstance(result, list):
                return []
        except Exception as e:
            logger.warning(f"Etherscan tokentx failed for {symbol}: {e}")
            return []

        out: list[WhaleTransfer] = []
        for tx in result[: self.max_per_asset]:
            try:
                raw_value = float(tx.get("value", "0"))
                token_decimals = int(tx.get("tokenDecimal") or 0 or decimals or 18)
                value = raw_value / (10 ** token_decimals)
            except (ValueError, TypeError):
                continue
            direction, confidence = classify_direction(self.chain, tx.get("from"), tx.get("to"))
            out.append(
                WhaleTransfer(
                    asset=symbol,
                    chain=self.chain,
                    value=value,
                    txid=tx.get("hash", ""),
                    from_addr=tx.get("from"),
                    to_addr=tx.get("to"),
                    source=self.name,
                    direction=direction,
                    direction_confidence=confidence,
                    raw=tx,
                )
            )
        return out


def default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT})