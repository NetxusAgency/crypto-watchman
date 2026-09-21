"""Secrets handling for broker connections.

Never store a plaintext access token/secret. Values are encrypted at rest with
Fernet (AES-128-CBC + HMAC) using a key derived from the app SECRET_KEY, so a
DB leak does not expose credentials by itself.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

__all__ = ["encrypt_secret", "decrypt_secret", "mask_secret", "secret_given"]


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str | None) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token_enc: str | None) -> str:
    if not token_enc:
        return ""
    # Tolerate an already-plaintext value in dev/test fixtures without crashing.
    if not token_enc.startswith("gAAAA"):
        return token_enc
    try:
        return _fernet().decrypt(token_enc.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:2]}…{'*' * 8}"


def secret_given(token_enc: str | None) -> bool:
    return bool(token_enc)