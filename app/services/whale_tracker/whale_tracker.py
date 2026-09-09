import httpx
import logging
from app.core.config import settings

logger = logging.getLogger("crypto_watchman.whale_tracker")

BTC_THRESHOLD = 10
ETH_THRESHOLD = 100

class WhaleTracker:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self.client.aclose()

    async def get_btc_whales(self) -> list[dict]:
        try:
            resp = await self.client.get("https://mempool.space/api/v1/blockchain")
            if resp.status_code != 200:
                logger.warning(f"mempool.space returned {resp.status_code}")
                return []
            data = resp.json()
            block_hash = data.get("tipHashKey", "")
            if not block_hash:
                return []
            block_resp = await self.client.get(f"https://mempool.space/api/block/{block_hash}/txs")
            if block_resp.status_code != 200:
                return []
            txs = block_resp.json()
            whales = []
            for tx in txs[:20]:
                total_out = sum(o.get("value", 0) for o in tx.get("vout", []))
                btc_value = total_out / 100_000_000
                if btc_value >= BTC_THRESHOLD:
                    txid = tx.get("txid", "")[:12] + "..."
                    whales.append({
                        "txid": txid,
                        "value": btc_value,
                        "asset": "BTC",
                        "source": "mempool.space",
                    })
            return whales
        except Exception as e:
            logger.error(f"Error fetching BTC whales: {e}")
        return []

    async def get_eth_whales(self) -> list[dict]:
        if not settings.ETHERSCAN_API_KEY:
            return []
        try:
            url = (
                f"https://api.etherscan.io/api"
                f"?module=proxy&action=eth_getBlockByNumber"
                f"&tag=latest&boolean=true&apikey={settings.ETHERSCAN_API_KEY}"
            )
            resp = await self.client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            txs = (data.get("result") or {}).get("transactions", [])
            whales = []
            for tx in txs:
                value_wei = int(tx.get("value", "0x0"), 16)
                value_eth = value_wei / 10**18
                if value_eth >= ETH_THRESHOLD:
                    txid = tx.get("hash", "")[:12] + "..."
                    to_addr = (tx.get("to") or "N/A")[:8] + "..."
                    whales.append({
                        "txid": txid,
                        "value": round(value_eth, 2),
                        "asset": "ETH",
                        "source": "Etherscan",
                        "to": to_addr,
                    })
            return whales
        except Exception as e:
            logger.error(f"Error fetching ETH whales: {e}")
        return []

    async def get_whales(self) -> list[dict]:
        btc = await self.get_btc_whales()
        eth = await self.get_eth_whales()
        all_whales = btc + eth
        all_whales.sort(key=lambda w: w["value"], reverse=True)
        return all_whales[:10]

whale_tracker = WhaleTracker()
