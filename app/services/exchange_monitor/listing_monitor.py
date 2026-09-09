import os
import httpx
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.database.models import User, Notification
from app.bot.dispatcher import bot
import redis.asyncio as aioredis

logger = logging.getLogger("crypto_watchman.listing_monitor")


class ListingMonitor:
    def __init__(self):
        self._pid = None
        self.client: httpx.AsyncClient | None = None
        self.redis_client = None

    async def _ensure_clients(self):
        """Recreate httpx / Redis clients when the OS PID changes (e.g. after
        a Celery worker fork).  The parent-process clients are bound to the
        parent's event loop which is closed in the child.
        """
        current_pid = os.getpid()
        if self.client is not None and self._pid == current_pid:
            return

        # Close stale clients from the previous process (best-effort)
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass
        if self.redis_client is not None:
            try:
                await self.redis_client.close()
            except Exception:
                pass

        self.client = httpx.AsyncClient(timeout=10.0)
        self.redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        self._pid = current_pid
        logger.info(f"Recreated httpx + Redis clients for PID {current_pid}")

    async def close(self):
        if self.client:
            await self.client.aclose()
        if self.redis_client:
            await self.redis_client.close()

    async def get_binance_symbols(self) -> set[str]:
        """Fetch all spot trading symbols on Binance."""
        try:
            resp = await self.client.get("https://api.binance.com/api/v3/exchangeInfo")
            if resp.status_code == 200:
                data = resp.json()
                return {item["symbol"] for item in data.get("symbols", []) if item["status"] == "TRADING"}
        except Exception as e:
            logger.error(f"Error fetching Binance symbols: {e}")
        return set()

    async def get_bybit_symbols(self) -> set[str]:
        """Fetch all spot trading symbols on Bybit."""
        try:
            resp = await self.client.get("https://api.bybit.com/v5/market/instruments-info?category=spot")
            if resp.status_code == 200:
                data = resp.json()
                return {item["symbol"] for item in data.get("result", {}).get("list", [])}
        except Exception as e:
            logger.error(f"Error fetching Bybit symbols: {e}")
        return set()

    async def get_coinbase_symbols(self) -> set[str]:
        """Fetch all spot trading products on Coinbase."""
        try:
            headers = {"User-Agent": "CryptoWatchmanBot/1.0"}
            resp = await self.client.get("https://api.exchange.coinbase.com/products", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {item["id"] for item in data}
        except Exception as e:
            logger.error(f"Error fetching Coinbase symbols: {e}")
        return set()

    async def alert_users(self, session: AsyncSession, exchange: str, symbol: str):
        """Send a telegram alert to all PRO and PREMIUM users for a new listing."""
        logger.info(f"🚨 New Listing Alert! {symbol} listed on {exchange}.")
        
        # Select all Pro and Premium users
        stmt = select(User).where(User.plan.in_(["pro", "premium"]))
        result = await session.execute(stmt)
        users = result.scalars().all()
        
        alert_text = (
            f"🚀 <b>New Exchange Listing detected!</b>\n\n"
            f"• <b>Asset / Pair:</b> <code>{symbol}</code>\n"
            f"• <b>Exchange:</b> {exchange}\n\n"
            f"<i>Stay ahead of the market!</i>"
        )
        
        for user in users:
            try:
                await bot.send_message(chat_id=user.telegram_id, text=alert_text, parse_mode="HTML")
                # Log notification
                notif = Notification(user_id=user.id, message=f"New Listing: {symbol} on {exchange}")
                session.add(notif)
            except Exception as e:
                logger.error(f"Failed to send listing alert to User ID {user.id}: {e}")
                
        await session.commit()

    async def check_listings(self, session: AsyncSession):
        """Compare current symbols against Redis cache. Detect and alert for new listings."""
        await self._ensure_clients()
        exchanges = {
            "Binance": self.get_binance_symbols,
            "Bybit": self.get_bybit_symbols,
            "Coinbase": self.get_coinbase_symbols
        }

        for exchange_name, fetch_func in exchanges.items():
            current_symbols = await fetch_func()
            if not current_symbols:
                continue

            cache_key = f"seen_listings:{exchange_name.lower()}"
            
            try:
                cache_exists = await self.redis_client.exists(cache_key)
            except Exception as e:
                logger.error(f"Redis unavailable for {exchange_name}: {e}. Skipping dedup.")
                continue
            
            if not cache_exists:
                logger.info(f"Initializing listing cache for {exchange_name} with {len(current_symbols)} symbols.")
                try:
                    await self.redis_client.sadd(cache_key, *current_symbols)
                except Exception as e:
                    logger.error(f"Redis sadd failed for {exchange_name}: {e}")
                continue

            try:
                seen_symbols = set(await self.redis_client.smembers(cache_key))
            except Exception as e:
                logger.error(f"Redis smembers failed for {exchange_name}: {e}")
                continue
            
            new_symbols = current_symbols - seen_symbols
            
            if new_symbols:
                for symbol in new_symbols:
                    await self.alert_users(session, exchange_name, symbol)
                    try:
                        await self.redis_client.sadd(cache_key, symbol)
                    except Exception as e:
                        logger.error(f"Redis sadd failed for {symbol} on {exchange_name}: {e}")
                    
# Singleton helper
listing_monitor = ListingMonitor()
