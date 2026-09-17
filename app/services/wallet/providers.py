import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger("crypto_watchman.wallet_providers")

SUPPORTED_NETWORKS = {
    "ethereum",
    "polygon",
    "bsc",
    "arbitrum",
    "optimism",
    "avalanche",
    "base",
}

COVALENT_CHAIN_IDS = {
    "ethereum": 1,
    "polygon": 137,
    "bsc": 56,
    "arbitrum": 42161,
    "optimism": 10,
    "avalanche": 43114,
    "base": 8453,
}

_NATIVE_ZERO_ADDR = "0x0000000000000000000000000000000000000000"


def normalize_network(network: str | None) -> str:
    net = (network or "ethereum").strip().lower()
    if net in ("eth", "ethereum"):
        return "ethereum"
    if net in ("polygon", "matic"):
        return "polygon"
    if net in ("bsc", "bnb", "binance"):
        return "bsc"
    if net in ("arbitrum", "arb"):
        return "arbitrum"
    if net in ("optimism", "op"):
        return "optimism"
    if net in ("avalanche", "avax"):
        return "avalanche"
    if net in ("base",):
        return "base"
    raise ValueError(
        f"Unsupported network '{network}'. Supported: "
        + ", ".join(sorted(SUPPORTED_NETWORKS))
    )


def is_valid_address(address: str) -> bool:
    import re

    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address.strip()))


@dataclass
class TokenBalance:
    symbol: str
    name: str
    contract_address: str
    quantity: float
    price_usd: float | None
    value_usd: float


def parse_covalent_items(items: list[dict]) -> list[TokenBalance]:
    """Map a Covalent balances_v2 items payload to TokenBalance rows."""
    balances: list[TokenBalance] = []
    for item in items or []:
        symbol = str(item.get("contract_ticker_symbol") or "").strip().upper()
        name = str(item.get("contract_name") or symbol or "Unknown").strip()
        if not symbol:
            continue
        decimals = int(item.get("contract_decimals") or 0)
        balance_raw = item.get("balance")
        try:
            quantity = float(balance_raw) / (10 ** decimals) if decimals else float(balance_raw or 0)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        raw_contract = str(item.get("contract_address") or "").strip().lower()
        contract = raw_contract if raw_contract and raw_contract != _NATIVE_ZERO_ADDR else "native"
        quote_rate = item.get("quote_rate")
        quote = item.get("quote")
        try:
            price_usd = float(quote_rate) if quote_rate is not None and quote_rate else None
            value_usd = float(quote) if quote is not None else None
        except (TypeError, ValueError):
            price_usd, value_usd = None, None
        if value_usd is None and price_usd:
            value_usd = quantity * price_usd
        balances.append(
            TokenBalance(
                symbol=symbol,
                name=name,
                contract_address=contract,
                quantity=round(quantity, 8),
                price_usd=price_usd,
                value_usd=round(value_usd or 0.0, 2),
            )
        )
    return balances


class WalletProvider(Protocol):
    async def fetch_balances(self, address: str, network: str) -> list[TokenBalance]:
        ...

    async def close(self) -> None:
        ...


class CovalentWalletProvider:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_balances(self, address: str, network: str) -> list[TokenBalance]:
        net = normalize_network(network)
        chain_id = COVALENT_CHAIN_IDS[net]
        url = f"https://api.covalenthq.com/v1/{chain_id}/address/{address}/balances_v2/"
        resp = await self.client.get(
            url,
            params={"key": self.api_key, "nft": "false", "no-spam": "true"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Covalent API returned {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        data = payload.get("data") or {}
        if data.get("error"):
            raise RuntimeError(str(data.get("error_message") or data["error"]))
        items = data.get("items") or []
        return parse_covalent_items(items)

    async def close(self) -> None:
        await self.client.aclose()


class PublicRpcProvider:
    """Keyless fallback: native ETH balance via a public RPC + CoinGecko price.

    ERC-20 tokens cannot be enumerated through a public RPC cheaply, so only the
    native balance is reported. Add COVALENT_API_KEY for full token coverage.
    """

    RPC_URL = "https://cloudflare-eth.com"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=20.0)

    async def fetch_balances(self, address: str, network: str) -> list[TokenBalance]:
        net = normalize_network(network)
        if net != "ethereum":
            raise RuntimeError(
                "The keyless fallback only supports Ethereum. Set COVALENT_API_KEY "
                "to connect wallets on other EVM networks."
            )
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_getBalance",
            "params": [address.lower(), "latest"],
            "id": 1,
        }
        resp = await self.client.post(self.RPC_URL, json=payload)
        resp.raise_for_status()
        result = resp.json().get("result")
        if not result:
            raise RuntimeError("Public RPC returned no balance — check the address.")
        quantity = int(result, 16) / 1e18
        if quantity <= 0:
            return []

        price_resp = await self.client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd"},
        )
        price = None
        try:
            price_resp.raise_for_status()
            price = price_resp.json().get("ethereum", {}).get("usd")
        except Exception as e:
            logger.warning(f"CoinGecko price fetch failed: {e}")

        return [
            TokenBalance(
                symbol="ETH",
                name="Ethereum",
                contract_address="native",
                quantity=round(quantity, 8),
                price_usd=price,
                value_usd=round(quantity * price, 2) if price else 0.0,
            )
        ]

    async def close(self) -> None:
        await self.client.aclose()