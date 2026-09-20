"""One-time browser login for the Mini App via the Telegram bot.

Flow:
  1. Mini App calls POST /api/auth/code -> we mint a short code (Redis key).
  2. User sends `/login CODE` to @bot in Telegram -> we mark the code approved.
  3. Mini App polls GET /api/auth/poll?code= -> once approved, receives a token.

Redis is used when available; an in-process dict keeps the flow working in
tests and local setups where Redis is not running.
"""

import logging
import secrets
import time

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY = "watchman:login_code:"
_APPROVED = "watchman:login_approved:"

_mem: dict[str, tuple[str, float]] = {}
_redis_client = None
_redis_tried = False


async def _redis_or_none():
    """Return a live Redis client or None (falling back to in-memory store)."""
    global _redis_client, _redis_tried
    if _redis_client is None and not _redis_tried:
        _redis_tried = True
        try:
            client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await client.ping()
            _redis_client = client
        except Exception as exc:  # noqa: BLE001 - Redis may simply be absent
            logger.warning("Redis unavailable; login codes kept in memory: %s", exc)
    return _redis_client


def _normalize(code: str) -> str:
    return "".join(code.upper().split()).strip()


async def create_login_code(ttl: int | None = None) -> str:
    time_to_live = ttl or settings.MINI_APP_LOGIN_CODE_TTL_SECONDS
    redis = await _redis_or_none()
    for _ in range(8):
        code = secrets.token_hex(3).upper()
        key = _KEY + code
        if redis is not None:
            ok = bool(await redis.set(key, "pending", ex=time_to_live, nx=True))
        else:
            if key in _mem:
                ok = False
            else:
                _mem[key] = ("pending", time.time() + time_to_live)
                ok = True
        if ok:
            return code
    raise RuntimeError("Could not allocate a login code")


async def redeem_login_code(code: str, telegram_id: int, approve_ttl: int = 120) -> bool:
    """Called by the /login command. Marks the code approved for telegram_id."""
    normalized = _normalize(code)
    if not normalized:
        return False
    pending_key = _KEY + normalized
    approved_key = _APPROVED + normalized
    redis = await _redis_or_none()
    if redis is not None:
        if await redis.get(pending_key) != "pending":
            return False
        await redis.delete(pending_key)
        await redis.set(approved_key, str(telegram_id), ex=approve_ttl)
        return True
    value, expires = _mem.get(pending_key, (None, 0.0))
    if value != "pending" or time.time() > expires:
        return False
    _mem.pop(pending_key, None)
    _mem[approved_key] = (str(telegram_id), time.time() + approve_ttl)
    return True


async def check_login_code(code: str) -> int | None:
    """Polled by the Mini App. Consumes the approval and returns telegram_id."""
    normalized = _normalize(code)
    if not normalized:
        return None
    key = _APPROVED + normalized
    redis = await _redis_or_none()
    if redis is not None:
        raw = await redis.get(key)
        if raw is None:
            return None
        await redis.delete(key)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    value, expires = _mem.get(key, (None, 0.0))
    if value is None or time.time() > expires:
        return None
    _mem.pop(key, None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None