import httpx
import logging
from app.core.config import settings

logger = logging.getLogger("crypto_watchman.price_fetcher")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

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
        self.client = httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT})

    async def close(self):
        await self.client.aclose()

    async def fetch_crypto_price(self, symbol: str) -> float | None:
        """Fetch cryptocurrency price with Binance -> Coinbase -> CoinGecko fallback chain."""
        symbol_upper = symbol.upper().strip()

        # 1. Try Binance (Fastest, unthrottled for spot pairs like BTCUSDT)
        try:
            pair = f"{symbol_upper}USDT"
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={pair}"
            resp = await self.client.get(url)
            if resp.status_code == 200:
                return float(resp.json()["price"])
        except Exception as e:
            logger.debug(f"Binance fetch failed for {symbol_upper}: {e}")

        # 2. Try Coinbase (Fallback 1)
        try:
            url = f"https://api.coinbase.com/v2/prices/{symbol_upper}-USD/spot"
            resp = await self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return float(data["data"]["amount"])
        except Exception as e:
            logger.debug(f"Coinbase fetch failed for {symbol_upper}: {e}")

        # 3. Fallback to CoinGecko
        cg_id = await self._resolve_cg_id(symbol_upper)
        if not cg_id:
            logger.warning(f"Could not map symbol {symbol_upper} to CoinGecko ID.")
            return None

        try:
            headers = {"User-Agent": USER_AGENT}
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
        symbol_upper = symbol.upper().replace("/", "").strip()
        
        # 1. Try TwelveData if API key is provided
        if settings.TWELVEDATA_API_KEY:
            try:
                url = f"https://api.twelvedata.com/price?symbol={symbol_upper}&apikey={settings.TWELVEDATA_API_KEY}"
                resp = await self.client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if "price" in data:
                        return float(data["price"])
            except Exception as e:
                logger.error(f"Error fetching forex price from TwelveData for {symbol_upper}: {e}")

        # 2. Fallback to open.er-api.com
        try:
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
                    elif base_currency == "USD" and target_currency in rates:
                        return float(rates[target_currency])
        except Exception as e:
            logger.error(f"Error fetching forex price from er-api fallback for {symbol_upper}: {e}")

        return None

    async def fetch_crypto_volume(self, symbol: str) -> float | None:
        """Fetch 24h trading volume for a cryptocurrency from CoinGecko."""
        cg_id = await self._resolve_cg_id(symbol)
        if not cg_id:
            return None

        try:
            headers = {"User-Agent": USER_AGENT}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_vol=true"
            resp = await self.client.get(url, headers=headers)
            if resp.status_code == 200:
                return float(resp.json()[cg_id].get("usd_24h_vol", 0))
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
            headers = {"User-Agent": USER_AGENT}
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
