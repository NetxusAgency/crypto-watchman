"""cTrader Open API OAuth (cTID) — authorize URL, code exchange, token refresh.

Connecting a cTrader account requires an Open API application (created at
id.ctrader.com -> Open API -> Your applications). The user clicks "Connect with
cTID" in the Mini App; we send them to the cTrader authorize page; they grant
account access; cTrader redirects to our callback URL with a `code`; we exchange
it for an access token + refresh token; both are stored (Fernet-encrypted) on
the BrokerConnection row. The background sweep + lazy pre-broker checks keep the
access token fresh using the refresh token.

Tokens issued by cTrader expire, so we store `token_expires_at` and ask for a new
access token whenever it is within REFRESH_SKEW_SECONDS of expiry.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.broker import decrypt_secret, encrypt_secret

logger = logging.getLogger("crypto_watchman.ctid_oauth")

__all__ = [
    "CTraderOAuthError",
    "PendingOAuth",
    "start_authorization",
    "build_authorize_url",
    "exchange_code",
    "refresh_access_token",
    "consume_pending_state",
]

# Refresh when the access token has this little runway left (seconds).
REFRESH_SKEW_SECONDS = 300
# A started authorization is only valid for this long (seconds).
STATE_TTL_SECONDS = 600
# The exchanged grant (the actual cTID auth flow) is just for the code → token hop.
TOKEN_TIMEOUT_SECONDS = 12.0


class CTraderOAuthError(RuntimeError):
    pass


@dataclass
class PendingOAuth:
    telegram_id: int
    mode: str
    label: str
    expires_at: float
    client_id_enc: str = ""
    client_secret_enc: str = ""


_pending: dict[str, PendingOAuth] = {}


def _authorize_url() -> str:
    return (settings.CTRADER_OAUTH_AUTHORIZE_URL or "").rstrip("/")


def _token_url() -> str:
    return (settings.CTRADER_OAUTH_TOKEN_URL or "").rstrip("/")


def build_authorize_url(*, client_id: str, state: str, redirect_uri: str | None = None) -> str:
    """The URL the user's browser opens on cTrader to grant account access."""
    if not client_id:
        raise CTraderOAuthError("Missing client_id - create an Open API application first.")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri or settings.CTRADER_OAUTH_REDIRECT_URI,
        "state": state,
    }
    return f"{_authorize_url()}?{urlencode(params)}"


def start_authorization(
    *,
    telegram_id: int,
    client_id: str,
    client_secret: str = "",
    mode: str = "demo",
    label: str = "",
) -> str:
    """Create a pending OAuth state and return the authorize URL for the user.

    The Open API application credentials are kept (Fernet-encrypted, never
    plaintext) on the pending state so the callback hop can use them for the
    code->token exchange without a second user round-trip.
    """
    state = secrets.token_urlsafe(24)
    _pending[state] = PendingOAuth(
        telegram_id=telegram_id,
        mode=(mode or "demo"),
        label=label or "",
        expires_at=time.time() + STATE_TTL_SECONDS,
        client_id_enc=encrypt_secret(client_id) if client_id else "",
        client_secret_enc=encrypt_secret(client_secret) if client_secret else "",
    )
    return build_authorize_url(client_id=client_id, state=state)


def consume_pending_state(state: str) -> PendingOAuth:
    """Validate + consume a one-time OAuth state. Raises CTraderOAuthError."""
    if not state:
        raise CTraderOAuthError("Missing state parameter.")
    pending = _pending.pop(state, None)
    if pending is None:
        raise CTraderOAuthError("Unknown or already-used OAuth state.")
    if time.time() > pending.expires_at:
        raise CTraderOAuthError("OAuth authorization expired - please try again.")
    return pending


async def _post_token(payload: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT_SECONDS) as client:
        resp = await client.post(_token_url(), data=payload)
    if resp.status_code >= 400:
        raise CTraderOAuthError(f"cTID token endpoint {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str | None = None,
) -> dict[str, Any]:
    """Swap the authorize-page `code` for tokens."""
    if not code or not client_id or not client_secret:
        raise CTraderOAuthError("Missing code, client_id or client_secret.")
    body = await _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri or settings.CTRADER_OAUTH_REDIRECT_URI,
        }
    )
    if not body.get("access_token"):
        raise CTraderOAuthError(f"Token response missing access_token: {body.get('error', body)}")
    return body


async def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Renew an expiring access token with the stored refresh token."""
    if not refresh_token or not client_id or not client_secret:
        raise CTraderOAuthError("Cannot refresh: refresh_token or client credentials missing.")
    body = await _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    )
    if not body.get("access_token"):
        raise CTraderOAuthError(f"Refresh response missing access_token: {body.get('error', body)}")
    return body


def expires_in_to_utc(expires_in: Any, *, now: datetime | None = None) -> datetime:
    """Convert an OAuth `expires_in` (seconds int/str) to a naive-UTC timestamp.

    Pure UTC wall-clock arithmetic (no ``timestamp()``) so the result compares
    correctly with :func:`token_is_expired` on any machine timezone.
    """
    seconds = int(expires_in or 0)
    base = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return base + timedelta(seconds=seconds)


def token_is_expired(connection, *, skew_seconds: int = REFRESH_SKEW_SECONDS) -> bool:
    """True when the stored access token should be refreshed before use."""
    expires = getattr(connection, "token_expires_at", None)
    if expires is None:
        return False  # unknown expiry (manual token) - try to use it
    if expires.tzinfo is not None:
        expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
    # Compare in naive-UTC wall-clock space so it is correct regardless of the
    # machine's local timezone (``timestamp()`` depends on local TZ).
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return expires < now_utc + timedelta(seconds=skew_seconds)


async def ensure_valid_token(session, connection) -> bool:
    """Refresh the connection's access token in place if it is near/after expiry.

    Returns True when the stored access token is usable (possibly just refreshed).
    Manual (non-OAuth) tokens with no expiry are left untouched. A failed refresh
    does NOT raise here — the caller falls back to the existing token or reports
    the failure.
    """
    if not token_is_expired(connection):
        return True
    refresh_enc = getattr(connection, "refresh_token_enc", "") or ""
    client_id_enc = getattr(connection, "client_id_enc", "") or ""
    client_secret_enc = getattr(connection, "client_secret_enc", "") or ""
    if not (refresh_enc and client_id_enc and client_secret_enc):
        logger.warning("cTID token expired for connection %s but no refresh credentials exist", connection.id)
        return False
    try:
        refresh_token = decrypt_secret(refresh_enc)
        client_id = decrypt_secret(client_id_enc)
        client_secret = decrypt_secret(client_secret_enc)
        body = await refresh_access_token(
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cTID refresh failed for connection %s: %s", connection.id, exc)
        return False

    new_access = body.get("access_token") or ""
    new_refresh = body.get("refresh_token") or refresh_token
    expires_at = expires_in_to_utc(body.get("expires_in", 3600))
    if new_access:
        connection.access_token_enc = encrypt_secret(new_access)
    if new_refresh:
        connection.refresh_token_enc = encrypt_secret(new_refresh)
    connection.token_expires_at = expires_at
    await session.flush()
    logger.info("Refreshed cTID access token for connection %s", connection.id)
    return bool(new_access)