from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return "pbkdf2_sha256$200000$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _encryption_key() -> bytes:
    raw = settings.encryption_master_key.encode("utf-8")
    return hashlib.sha256(raw).digest()


def encrypt_secret(value: str) -> Tuple[str, str, str]:
    nonce = os.urandom(12)
    aes = AESGCM(_encryption_key())
    ciphertext = aes.encrypt(nonce, value.encode("utf-8"), None)
    return (
        base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        base64.urlsafe_b64encode(nonce).decode("ascii"),
        "v1",
    )


def decrypt_secret(ciphertext_b64: str, nonce_b64: str) -> str:
    aes = AESGCM(_encryption_key())
    ciphertext = base64.urlsafe_b64decode(ciphertext_b64.encode("ascii"))
    nonce = base64.urlsafe_b64decode(nonce_b64.encode("ascii"))
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return value[:2] + "****"
    return value[:6] + "****" + value[-4:]
