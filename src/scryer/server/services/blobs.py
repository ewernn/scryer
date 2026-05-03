"""R2 (Cloudflare object storage) client wrapper.

Per plan §15 #17: write-before-commit ordering — call put_blob() FIRST, then
flush the PG row that points at the blob. If R2 fails, no PG row is created
and we never commit a dangling pointer. Orphan blobs (R2 succeeded but PG
flush failed afterward) are cleaned up by a future GC pass.

Path-based keys, scoped per workspace and project for blast-radius isolation:

    r2://{bucket}/ws/{workspace_id}/proj/{project_id}/{kind}/{relpath}

Where `kind` is one of: trajectories, results, archives.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from scryer.config import get_settings
from scryer.server.services.errors import ValidationError


def make_blob_key(
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    relpath: str,
) -> str:
    """Build the canonical R2 key for an object. Caller responsible for
    sanitizing relpath (no '..', no leading '/'). The kind+ws+proj prefix is
    enforced so a misuse can't cross-pollute namespaces."""
    if ".." in relpath or relpath.startswith("/"):
        raise ValidationError(f"Invalid blob relpath: {relpath!r}")
    if kind not in ("trajectories", "results", "archives"):
        raise ValidationError(f"Unknown blob kind: {kind!r}")
    return f"ws/{workspace_id}/proj/{project_id}/{kind}/{relpath}"


@lru_cache(maxsize=1)
def _get_client() -> Any:
    s = get_settings()
    if not (s.r2_endpoint and s.r2_access_key_id and s.r2_secret_access_key):
        raise RuntimeError(
            "R2 credentials missing — set R2_ENDPOINT, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY env vars."
        )
    return boto3.client(
        "s3",
        endpoint_url=s.r2_endpoint,
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        region_name="auto",
        config=BotoConfig(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def _bucket() -> str:
    return get_settings().r2_bucket_name


# ── Blob I/O ────────────────────────────────────────────────────────────────


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def put_blob(
    key: str, body: bytes, *, content_type: str = "application/octet-stream"
) -> tuple[str, str]:
    """Upload `body` to R2 at `key`. Returns (uri, sha256_hex). Synchronous
    boto3 call wrapped via asyncio.to_thread."""
    sha = sha256_hex(body)
    bucket = _bucket()
    await asyncio.to_thread(
        _get_client().put_object,
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    return f"r2://{bucket}/{key}", sha


async def get_blob(uri: str) -> bytes:
    """Fetch a blob given its r2:// URI. Verifies bucket matches the
    configured one — won't fetch from arbitrary buckets."""
    expected = f"r2://{_bucket()}/"
    if not uri.startswith(expected):
        raise ValidationError(f"Refusing to fetch from foreign URI: {uri!r}")
    key = uri[len(expected) :]
    resp = await asyncio.to_thread(_get_client().get_object, Bucket=_bucket(), Key=key)
    body: bytes = await asyncio.to_thread(resp["Body"].read)
    return body


async def delete_blob(uri: str) -> None:
    expected = f"r2://{_bucket()}/"
    if not uri.startswith(expected):
        raise ValidationError(f"Refusing to delete from foreign URI: {uri!r}")
    key = uri[len(expected) :]
    await asyncio.to_thread(_get_client().delete_object, Bucket=_bucket(), Key=key)
