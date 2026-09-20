"""Authentication for the Telegram Mini App (Phase 2E).

Two layers:
1. `validate_init_data` — verifies the `initData` string Telegram injects into
   `window.Telegram.WebApp` using the documented HMAC-SHA256 scheme.
2. `issue_token` / `parse_token` — short-lived signed app tokens so the frontend
   does not have to re-send initData (and re-validate it) on every API call.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse

from app.core.config import settings

_INIT_DATA_TTL_SECONDS = 24 * 3600


def _hmac_sha256(key: bytes, payload: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def _webapp_secret_key(bot_token: str) -> bytes:
    return _hmac_sha256(b"WebAppData", bot_token.encode())


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate a Telegram WebApp initData string.

    Returns the parsed fields (including the `user` object) when the signature
    and freshness checks pass, otherwise None.
    """
    if not init_data or not bot_token or bot_token == "placeholder_token":
        return None
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    provided_hash = params.pop("hash", None)
    if not provided_hash:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )
    expected = _hmac_sha256(_webapp_secret_key(bot_token), data_check_string.encode()).hex()
    if not hmac.compare_digest(expected, provided_hash):
        return None

    try:
        auth_date = int(params.get("auth_date", "0"))
    except ValueError:
        return None
    if abs(time.time() - auth_date) > _INIT_DATA_TTL_SECONDS:
        return None

    try:
        user = json.loads(params["user"]) if params.get("user") else None
    except (json.JSONDecodeError, TypeError):
        return None
    if not user or not user.get("id"):
        return None

    return {"user": user, "auth_date": auth_date}


def issue_token(telegram_id: int, ttl_hours: int | None = None) -> str:
    """Sign `telegram_id:expiry` with SECRET_KEY and base64url-encode it."""
    ttl = ttl_hours or settings.MINI_APP_TOKEN_TTL_HOURS
    expiry = int(time.time()) + ttl * 3600
    payload = f"{telegram_id}:{expiry}"
    sig = hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()
    return raw.rstrip("=")


def parse_token(token: str | None) -> int | None:
    """Return the telegram_id if the app token is valid and unexpired, else None."""
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
        payload, separator, sig = decoded.rpartition(":")
        if not separator:
            return None
        expected = hmac.new(
            settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        tg_id, sep, expiry = payload.partition(":")
        if not sep or int(expiry) < time.time():
            return None
        return int(tg_id)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None