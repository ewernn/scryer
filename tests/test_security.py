"""Unit tests for security primitives."""

from __future__ import annotations

import hashlib
import time
from uuid import uuid4

import jwt
import pytest

from scryer.server.services.security import (
    decrypt_credential,
    encrypt_credential,
    generate_api_key,
    generate_invitation_token,
    hash_api_key,
    issue_access_jwt,
    verify_access_jwt,
    verify_api_key,
)


def test_generate_api_key_format() -> None:
    full, prefix, hashed = generate_api_key()
    assert full.startswith("scrk_live_")
    assert prefix == full[:16]
    assert hashed == hashlib.sha256(full.encode()).hexdigest()
    assert len(hashed) == 64


def test_hash_and_verify_api_key_roundtrip() -> None:
    full, _, hashed = generate_api_key()
    assert hash_api_key(full) == hashed
    assert verify_api_key(full, hashed) is True
    assert verify_api_key("scrk_live_wrong", hashed) is False


def test_verify_api_key_constant_time_no_obvious_leak() -> None:
    # Sanity: timing should not vary wildly between matching and mismatching
    # keys at byte-zero vs full-length. Not a rigorous statistical test —
    # just ensures hmac.compare_digest is being used.
    full, _, hashed = generate_api_key()
    bad_early = "x" + hashed[1:]
    bad_late = hashed[:-1] + "x"

    def _bench(target: str) -> float:
        t0 = time.perf_counter_ns()
        for _ in range(2000):
            verify_api_key(full, target)
        return time.perf_counter_ns() - t0

    early = _bench(bad_early)
    late = _bench(bad_late)
    ratio = max(early, late) / max(min(early, late), 1)
    assert ratio < 5.0


def test_generate_invitation_token_format() -> None:
    full, hashed = generate_invitation_token()
    assert full.startswith("scrinv_")
    assert hashed == hashlib.sha256(full.encode()).hexdigest()
    assert len(hashed) == 64


def test_encrypt_decrypt_credential_roundtrip() -> None:
    plaintext = f"secret-{uuid4().hex}"
    blob, version = encrypt_credential(plaintext)
    assert blob != plaintext
    assert decrypt_credential(blob, version) == plaintext


def test_encrypt_credential_nonce_randomness() -> None:
    plaintext = "same-plaintext"
    a, _ = encrypt_credential(plaintext)
    b, _ = encrypt_credential(plaintext)
    assert a != b


def test_jwt_issue_and_verify_roundtrip() -> None:
    user_id = str(uuid4())
    token = issue_access_jwt(user_id)
    claims = verify_access_jwt(token)
    assert claims["sub"] == user_id
    assert claims["typ"] == "access"


def test_jwt_expired_raises() -> None:
    # ttl=-30 to clear the leeway=10 in verify_access_jwt
    token = issue_access_jwt(str(uuid4()), ttl_seconds=-30)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_access_jwt(token)
