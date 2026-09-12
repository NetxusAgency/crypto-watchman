# Symbol -> canonical name used to build search queries (avoids bare-ticker noise).
ASSET_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "ADA": "Cardano",
    "DOT": "Polkadot",
    "DOGE": "Dogecoin",
    "XRP": "Ripple",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "MATIC": "Polygon",
    "POL": "Polygon",
    "TRX": "Tron",
    "LTC": "Litecoin",
    "NEAR": "Near Protocol",
    "UNI": "Uniswap",
    "ATOM": "Cosmos",
    "FTM": "Fantom",
    "SAND": "The Sandbox",
    "MANA": "Decentraland",
    "AAVE": "Aave",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "SUI": "Sui",
    "SEI": "Sei",
    "INJ": "Injective",
    "TON": "Toncoin",
    "SHIB": "Shiba Inu",
    "PEPE": "Pepe",
    "WIF": "Dogwifhat",
    "BONK": "Bonk",
    "WLD": "Worldcoin",
    "XLM": "Stellar",
    "ALGO": "Algorand",
    "FIL": "Filecoin",
    "HBAR": "Hedera",
    "VET": "VeChain",
    "ICP": "Internet Computer",
    "EOS": "EOS",
    "XTZ": "Tezos",
    "EGLD": "MultiversX",
    "FLOW": "Flow",
    "MKR": "Maker",
    "COMP": "Compound",
    "GRT": "The Graph",
    "RUNE": "THORChain",
    "GALA": "Gala",
    "AXS": "Axie Infinity",
    "CHZ": "Chiliz",
}

# Symbols that are not news-relevant (fiat pairs like EURUSD).
FIAT_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF", "EURGBP", "EURJPY", "GBPJPY"}


def is_news_symbol(symbol: str) -> bool:
    sym = symbol.upper()
    if sym in FIAT_SYMBOLS:
        return False
    if len(sym) == 6 and sym.isalpha():
        return False
    return True


def asset_name(symbol: str) -> str:
    return ASSET_NAMES.get(symbol.upper(), symbol.capitalize())


def build_queries(symbol: str, max_queries: int = 3) -> list[str]:
    """Build Google-News-friendly search queries avoiding bare tickers."""
    sym = symbol.upper()
    name = asset_name(sym)
    if name.lower() != sym.lower():
        templates = [
            f'"{name}" crypto',
            f'"{name}" news',
            f'"{name}" catalyst OR regulation OR listing OR hack OR upgrade',
        ]
        return templates[:max_queries]
    return [f"{sym} crypto news"]