"""R2 blob round-trip tests. Uses the real R2 bucket — requires R2_*
env vars to be set (skipped otherwise so CI without creds doesn't break).

Each test uses a unique key prefix and cleans up after itself."""

from __future__ import annotations

import uuid

import pytest

from scryer.config import get_settings
from scryer.server.services.blobs import (
    delete_blob,
    get_blob,
    make_blob_key,
    put_blob,
    sha256_hex,
)
from scryer.server.services.errors import ValidationError

pytestmark = pytest.mark.skipif(
    not get_settings().r2_endpoint or not get_settings().r2_access_key_id,
    reason="R2 env vars not configured",
)


def _unique_key() -> str:
    return make_blob_key(
        workspace_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        kind="trajectories",
        relpath=f"_test/{uuid.uuid4().hex}.json",
    )


async def test_put_get_round_trip() -> None:
    key = _unique_key()
    payload = b'{"hello": "world"}'
    uri, sha = await put_blob(key, payload, content_type="application/json")
    assert sha == sha256_hex(payload)
    assert uri.startswith("r2://")
    try:
        got = await get_blob(uri)
        assert got == payload
    finally:
        await delete_blob(uri)


async def test_get_blob_rejects_foreign_bucket() -> None:
    with pytest.raises(ValidationError):
        await get_blob("r2://some-other-bucket/key")


async def test_make_blob_key_rejects_traversal() -> None:
    with pytest.raises(ValidationError):
        make_blob_key(
            workspace_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            kind="trajectories",
            relpath="../../etc/passwd",
        )


async def test_make_blob_key_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        make_blob_key(
            workspace_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            kind="not-a-kind",
            relpath="x.json",
        )
