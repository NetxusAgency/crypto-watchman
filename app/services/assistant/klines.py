import logging
import time
from typing import Sequence

import httpx

from app.core.config import settings
from app.services.assistant.indicators import Candle
from app.services.prices.price_fetcher import CRYPTO_MAPPING, USER_AGENT, price_fetcher

logger = logging.getLogger("crypto_watchman.assistant_klines")

# Supported intervals mapped to standard formats
INTERVAL_MAP = {
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


class KlineFetcher:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT})

    async def close(self):
        await self.client.aclose()

    async def fetch_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[Candle]:
        """Fetch OHLCV candles with Binance -> CoinGecko -> TwelveData -> Synthetic fallback."""
        symbol_clean = symbol.upper().strip()
        tf = INTERVAL_MAP.get(timeframe.lower(), "1h")

        # 1. Binance Public Kline API (Fastest and standard for crypto)
        candles = await self._fetch_binance(symbol_clean, tf, limit)
        if candles:
            return candles

        # 2. TwelveData for Forex if configured
        if len(symbol_clean) == 6 and not symbol_clean.endswith("USDT"):
            candles = await self._fetch_twelvedata(symbol_clean, tf, limit)
            if candles:
                return candles

        # 3. CoinGecko OHLC API Fallback
        candles = await self._fetch_coingecko_ohlc(symbol_clean, tf)
        if candles:
            return candles

        # 4. Fallback: Generate candles around current live price
        return await self._generate_fallback_candles(symbol_clean, limit)

    async def _fetch_binance(self, symbol: str, interval: str, limit: int) -> list[Candle] | None:
        try:
            pair = f"{symbol}USDT"
            url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval={interval}&limit={limit}"
            resp = await self.client.get(url)
            if resp.status_code == 200:
                raw_candles = resp.json()
                candles = []
                for c in raw_candles:
                    candles.append(
                        Candle(
                            timestamp=int(c[0]),
                            open=float(c[1]),
                            high=float(c[2]),
                            low=float(c[3]),
                            close=float(c[4]),
                            volume=float(c[5]),
                        )
                    )
                if candles:
                    return candles
        except Exception as e:
            logger.debug(f"Binance kline fetch failed for {symbol}: {e}")
        return None

    async def _fetch_twelvedata(self, symbol: str, interval: str, limit: int) -> list[Candle] | None:
        if not settings.TWELVEDATA_API_KEY:
            return None
        try:
            formatted_sym = f"{symbol[:3]}/{symbol[3:]}"
            td_interval = "15min" if interval == "15m" else ("1h" if interval == "1h" else ("4h" if interval == "4h" else "1day"))
            url = (
                f"https://api.twelvedata.com/time_series?symbol={formatted_sym}"
                f"&interval={td_interval}&outputsize={limit}&apikey={settings.TWELVEDATA_API_KEY}"
            )
            resp = await self.client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if "values" in data:
                    candles = []
                    for c in reversed(data["values"]):
                        candles.append(
                            Candle(
                                timestamp=int(time.time() * 1000),
                                open=float(c["open"]),
                                high=float(c["high"]),
                                low=float(c["low"]),
                                close=float(c["close"]),
                                volume=float(c.get("volume", 0.0) or 0.0),
                            )
                        )
                    if candles:
                        return candles
        except Exception as e:
            logger.debug(f"TwelveData kline fetch failed for {symbol}: {e}")
        return None

    async def _fetch_coingecko_ohlc(self, symbol: str, interval: str) -> list[Candle] | None:
        cg_id = CRYPTO_MAPPING.get(symbol) or await price_fetcher._resolve_cg_id(symbol)
        if not cg_id:
            return None
        try:
            days = 1 if interval in ("15m", "1h") else (7 if interval == "4h" else 30)
            headers = {"User-Agent": USER_AGENT}
            if settings.COINGECKO_API_KEY:
                headers["x-cg-demo-api-key"] = settings.COINGECKO_API_KEY
            url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc?vs_currency=usd&days={days}"
            resp = await self.client.get(url, headers=headers)
            if resp.status_code == 200:
                raw_ohlc = resp.json()
                candles = []
                for c in raw_ohlc:
                    candles.append(
                        Candle(
                            timestamp=int(c[0]),
                            open=float(c[1]),
                            high=float(c[2]),
                            low=float(c[3]),
                            close=float(c[4]),
                            volume=1.0,
                        )
                    )
                if candles:
                    return candles
        except Exception as e:
            logger.debug(f"CoinGecko OHLC fetch failed for {symbol}: {e}")
        return None

    async def _generate_fallback_candles(self, symbol: str, limit: int = 50) -> list[Candle]:
        """Generate synthetic candle data around the current market price when feeds are offline."""
        current_price = await price_fetcher.get_price(symbol) or 100.0
        candles = []
        now_ms = int(time.time() * 1000)
        step_ms = 3600 * 1000  # 1h step

        price = current_price * 0.98
        for i in range(limit):
            t = now_ms - (limit - i) * step_ms
            # Small random-walk variation
            drift = 1.0 + ((i % 5) - 2) * 0.003
            open_p = price
            close_p = open_p * drift
            high_p = max(open_p, close_p) * 1.004
            low_p = min(open_p, close_p) * 0.996
            price = close_p
            candles.append(
                Candle(
                    timestamp=t,
                    open=round(open_p, 4),
                    high=round(high_p, 4),
                    low=round(low_p, 4),
                    close=round(close_p, 4),
                    volume=1000.0 + (i * 10),
                )
            )
        # Ensure latest close matches current price
        candles[-1].close = current_price
        return candles


kline_fetcher = KlineFetcher()
