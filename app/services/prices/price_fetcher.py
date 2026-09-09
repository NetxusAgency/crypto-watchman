import httpx
import logging
from app.core.config import settings

logger = logging.getLogger("crypto_watchman.price_fetcher")

# Mapping popular tickers to CoinGecko API IDs
CRYPTO_MAPPING = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "DOT": "polkadot",
    "DOGE": "dogecoin",
    "XRP": "ripple",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "MATIC": "polygon",
    "TRX": "tron",
    "LTC": "litecoin",
    "NEAR": "near",
    "UNI": "uniswap",
    "ICP": "internet-computer"
}

class PriceFetcher:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self.client.aclose()

    async def fetch_crypto_price(self, symbol: str) -> float | None:
        """Fetch cryptocurrency price from CoinGecko API."""
        cg_id = await self._resolve_cg_id(symbol)
        if not cg_id:
            logger.warning(f"Could not map symbol {symbol} to CoinGecko ID.")
            return None

        try:
            headers = {}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            resp = await self.client.get(url, headers=headers)
            if resp.status_code == 200:
                return float(resp.json()[cg_id]["usd"])
            logger.error(f"CoinGecko API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error fetching crypto price for {symbol}: {e}")
        return None

    async def fetch_forex_price(self, symbol: str) -> float | None:
        """Fetch forex pair price from TwelveData, with a free API fallback (open.er-api.com)."""
        symbol_upper = symbol.upper().replace("/", "").strip() # e.g., EURUSD or EUR/USD -> EURUSD
        
        # 1. Try TwelveData if API key is provided
        if settings.TWELVEDATA_API_KEY:
            try:
                url = f"https://api.twelvedata.com/price?symbol={symbol_upper}&apikey={settings.TWELVEDATA_API_KEY}"
                resp = await self.client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if "price" in data:
                        return float(data["price"])
                    else:
                        logger.warning(f"TwelveData error: {data}")
            except Exception as e:
                logger.error(f"Error fetching forex price from TwelveData for {symbol_upper}: {e}")

        # 2. Fallback to open.er-api.com (No API key needed, gets exchange rates relative to USD)
        try:
            # We assume symbols are typically formatted like EURUSD (EUR/USD) or GBPUSD
            # We slice the first 3 chars as base and next 3 as target
            if len(symbol_upper) == 6:
                base_currency = symbol_upper[:3]
                target_currency = symbol_upper[3:]
                
                url = f"https://open.er-api.com/v6/latest/{base_currency}"
                resp = await self.client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    rates = data.get("rates", {})
                    if target_currency in rates:
                        return float(rates[target_currency])
                    # If base is USD, target is base_currency in the denominator
                    elif base_currency == "USD" and target_currency in rates:
                        return float(rates[target_currency])
        except Exception as e:
            logger.error(f"Error fetching forex price from free er-api fallback for {symbol_upper}: {e}")

        return None

    async def fetch_crypto_volume(self, symbol: str) -> float | None:
        """Fetch 24h trading volume for a cryptocurrency from CoinGecko."""
        cg_id = await self._resolve_cg_id(symbol)
        if not cg_id:
            logger.warning(f"Could not map symbol {symbol} to CoinGecko ID for volume.")
            return None

        try:
            headers = {}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_vol=true"
            resp = await self.client.get(url, headers=headers)
            if resp.status_code == 200:
                return float(resp.json()[cg_id].get("usd_24h_vol", 0))
            logger.error(f"CoinGecko volume API returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error fetching crypto volume for {symbol}: {e}")
        return None

    async def _resolve_cg_id(self, symbol: str) -> str | None:
        symbol_upper = symbol.upper().strip()
        cg_id = CRYPTO_MAPPING.get(symbol_upper)
        if cg_id:
            return cg_id
        try:
            search_url = f"https://api.coingecko.com/api/v3/search?query={symbol_upper}"
            headers = {}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            resp = await self.client.get(search_url, headers=headers)
            if resp.status_code == 200:
                coins = resp.json().get("coins", [])
                if coins:
                    cg_id = coins[0]["id"]
                    CRYPTO_MAPPING[symbol_upper] = cg_id
                    return cg_id
        except Exception as e:
            logger.error(f"Error searching CoinGecko ID for {symbol}: {e}")
        return None

    async def fetch_volatility_zscore(self, symbol: str) -> float | None:
        cg_id = await self._resolve_cg_id(symbol)
        if not cg_id:
            return None
        try:
            url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days=30"
            resp = await self.client.get(url, timeout=15.0)
            if resp.status_code != 200:
                logger.error(f"CoinGecko history returned {resp.status_code} for {symbol}")
                return None
            prices = resp.json().get("prices", [])
            if len(prices) < 7:
                return None
            closes = [p[1] for p in prices]
            returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 for i in range(1, len(closes))]
            if len(returns) < 3:
                return None
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            std = variance ** 0.5
            if std == 0:
                return None
            latest_return = returns[-1]
            return abs(latest_return - mean) / std
        except Exception as e:
            logger.error(f"Error fetching volatility for {symbol}: {e}")
        return None

    async def get_price(self, symbol: str) -> float | None:
        """General method to fetch asset price, detecting if crypto or forex."""
        symbol_clean = symbol.upper().strip()
        
        # Simple heuristic: if length is 6 and contains common fiat codes, or has '/' -> Forex
        fiat_codes = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
        is_forex = False
        
        if "/" in symbol_clean:
            is_forex = True
        elif len(symbol_clean) == 6:
            base = symbol_clean[:3]
            target = symbol_clean[3:]
            if base in fiat_codes or target in fiat_codes:
                is_forex = True
                
        if is_forex:
            return await self.fetch_forex_price(symbol_clean)
        else:
            return await self.fetch_crypto_price(symbol_clean)

# Singleton helper
price_fetcher = PriceFetcher()
