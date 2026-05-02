"""Security primitives: password hashing, token generation, AES-GCM encryption,
JWT issue/verify.

All decisions per investigator returns (see notepad). Pure functions; the
service layer composes them.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scryer.config import get_settings

# OWASP 2025-ish parameters; ~50ms on modern hardware.
_PH = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


# ─── Password hashing (argon2id) ─────────────────────────────────────────────


async def hash_password(plaintext: str) -> str:
    """Return PHC-encoded argon2id hash. Run in thread (CPU-bound)."""
    return await asyncio.to_thread(_PH.hash, plaintext)


async def verify_password(stored_hash: str, plaintext: str) -> bool:
    """Returns True iff plaintext matches. Catches all argon2 errors (bad
    PHC string, mismatch, invalid hash) — never leaks via 500."""
    try:
        return await asyncio.to_thread(_PH.verify, stored_hash, plaintext)
    except (VerifyMismatchError, InvalidHash, VerificationError):
        return False


async def password_needs_rehash(stored_hash: str) -> bool:
    """True if the stored hash uses outdated parameters; rehash on next login."""
    return await asyncio.to_thread(_PH.check_needs_rehash, stored_hash)


# ─── Token generation + hashing ──────────────────────────────────────────────


def generate_api_key(prefix: str = "scrk_live") -> tuple[str, str, str]:
    """Generate a new API key.

    Returns: (full_key, key_prefix_for_display, sha256_hex_for_storage)

    Format: `<prefix>_<43 b64url chars>` ≈ 256 bits entropy.
    `key_prefix` is the first 16 chars of the full key (indexed for lookup).
    `key_hash` is sha256(full_key); high entropy → no need for argon2.
    """
    raw = secrets.token_bytes(32)
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    full_key = f"{prefix}_{body}"
    key_prefix = full_key[:16]
    key_hash = hashlib.sha256(full_key.encode("utf-8")).hexdigest()
    return full_key, key_prefix, key_hash


def hash_api_key(full_key: str) -> str:
    """sha256 hex of an API key. Use for verification at lookup time."""
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def verify_api_key(full_key: str, stored_hash: str) -> bool:
    """Constant-time comparison of provided key against stored hash."""
    return hmac.compare_digest(stored_hash, hash_api_key(full_key))


def generate_invitation_token() -> tuple[str, str]:
    """Generate an invite token. Returns (full_token, sha256_hex_for_storage).

    Format: `scrinv_<40 hex>` = 160 bits entropy.
    """
    body = secrets.token_hex(20)
    full_token = f"scrinv_{body}"
    token_hash = hashlib.sha256(full_token.encode("utf-8")).hexdigest()
    return full_token, token_hash


def hash_invitation_token(full_token: str) -> str:
    return hashlib.sha256(full_token.encode("utf-8")).hexdigest()


# ─── AES-GCM (Credential encryption) ────────────────────────────────────────


def _key_for_version(version: int) -> bytes:
    """Resolve an encryption key for a version.

    Currently only `current` version is supported (one key in env). Plumbing
    for older versions via `ENCRYPTION_KEY_V<N>` env vars lands when first
    rotation happens.
    """
    s = get_settings()
    if version != s.encryption_key_version:
        raise ValueError(
            f"Encryption key version {version} not loaded (current={s.encryption_key_version})"
        )
    if not s.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY env var is empty")
    return bytes.fromhex(s.encryption_key)


def encrypt_credential(plaintext: str, *, aad: bytes | None = None) -> tuple[str, int]:
    """AES-256-GCM encrypt. Returns (base64-encoded blob, key_version).

    Blob = base64(nonce(12) || ciphertext || tag(16)).
    """
    s = get_settings()
    key = _key_for_version(s.encryption_key_version)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    blob = base64.b64encode(nonce + ct).decode("ascii")
    return blob, s.encryption_key_version


def decrypt_credential(blob_b64: str, key_version: int, *, aad: bytes | None = None) -> str:
    key = _key_for_version(key_version)
    raw = base64.b64decode(blob_b64)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, aad).decode("utf-8")


# ─── JWT (human session auth) ────────────────────────────────────────────────


JWT_ISSUER = "scryer"
JWT_AUDIENCE = "scryer-api"


def issue_access_jwt(user_id: str, *, ttl_seconds: int | None = None) -> str:
    s = get_settings()
    if not s.jwt_secret:
        raise RuntimeError("JWT_SECRET env var is empty")
    ttl = ttl_seconds if ttl_seconds is not None else s.jwt_ttl_seconds
    now = datetime.now(UTC)
    claims = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "typ": "access",
    }
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256")


def verify_access_jwt(token: str) -> dict[str, Any]:
    s = get_settings()
    if not s.jwt_secret:
        raise RuntimeError("JWT_SECRET env var is empty")
    return jwt.decode(
        token,
        s.jwt_secret,
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        leeway=10,
    )
