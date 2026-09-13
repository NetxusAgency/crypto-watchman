"""Buy/sell direction heuristics for whale transactions.

On-chain flows are ambiguous by nature (custody moves, OTC settlements), so we
only claim a direction when an address is confidently tagged:

* DEX router contracts (ERC-20 + native ETH) — deterministic: tokens flowing
  *into* a known router are sells, tokens flowing *out* are buys.
* Major known exchange deposit/withdrawal wallets — medium confidence.

Otherwise the direction is reported as TRANSFER (no claim).
"""

from __future__ import annotations

DEX_ROUTER_ADDRESSES: frozenset[str] = frozenset({
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # Uniswap V2 Router02
    "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3 SwapRouter
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",  # Uniswap V3 SwapRouter02
    "0x1111111254fb6c44bac0bed2854e76f90643097d",  # 1inch v5
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",  # SushiSwap Router
})

# Widely-cited major exchange wallets — treated as medium-confidence tags only.
EXCHANGE_BTC_ADDRESSES: frozenset[str] = frozenset({
    "34xp4vrocgjym3xr7ycvpfhocnxv4twseo",  # Binance BTC hot wallet
    "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h",  # Coinbase BTC
})

EXCHANGE_ETH_ADDRESSES: frozenset[str] = frozenset({
    "28c6c06298d514db089934071355e5743bf21d60",  # Binance ETH hot wallet
    "71660c4005ba85c37ccec55d0c4493e66fe775d3",  # Coinbase ETH
})

# Additional tagged-address sources can be wired in later without changing the
# classification contract here.


def classify_direction(chain: str, from_addr: str | None, to_addr: str | None) -> tuple[str, str]:
    """Return (direction, confidence). direction is BUY / SELL / TRANSFER.

    confidence: DEX match -> high; exchange match -> medium; else low.
    """
    f = (from_addr or "").lower()
    t = (to_addr or "").lower()
    chain_key = {
        "btc": "bitcoin",
        "eth": "ethereum",
    }.get((chain or "").lower(), (chain or "").lower())

    if not f and not t:
        return "TRANSFER", "low"

    # DEX flows: token delivered from router = a buy; token sent to router = a sell.
    if t in DEX_ROUTER_ADDRESSES:
        return "SELL", "high"
    if f in DEX_ROUTER_ADDRESSES:
        return "BUY", "high"

    # Exchange flows: deposit to an exchange wallet = sell pressure; withdrawal = buy.
    if chain_key == "bitcoin":
        if t in EXCHANGE_BTC_ADDRESSES:
            return "SELL", "medium"
        if f in EXCHANGE_BTC_ADDRESSES:
            return "BUY", "medium"
    elif chain_key == "ethereum":
        if t in EXCHANGE_ETH_ADDRESSES:
            return "SELL", "medium"
        if f in EXCHANGE_ETH_ADDRESSES:
            return "BUY", "medium"

    return "TRANSFER", "low"


DIRECTION_LABELS: dict[str, str] = {
    "BUY": "🟢 BUY",
    "SELL": "🔴 SELL",
    "TRANSFER": "⚪ Transfer",
}