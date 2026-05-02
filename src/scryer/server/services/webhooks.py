"""Webhook delivery worker: signs payload, fires HTTP, retries on failure.

Per plan §15:
- HMAC-SHA256 signature in `X-Scryer-Signature` header
- Exponential backoff: 1s, 10s, 100s, 1000s
- Dead-letter after 4 failed attempts
- DNS re-resolution at fire time (rebinding defense)
- SSRF denylist: RFC 5735 private ranges blocked
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from scryer.server.models.audit import Webhook, WebhookDelivery
from scryer.server.models.enums import WebhookDeliveryStatus
from scryer.server.services.errors import NotFoundError, ValidationError

_BACKOFF_SECONDS = [1, 10, 100, 1000]
_MAX_ATTEMPTS = len(_BACKOFF_SECONDS)
_DELIVERY_TIMEOUT_S = 10


async def create_webhook(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    url: str,
    event_types: list[str],
) -> Webhook:
    _validate_url_at_creation(url)
    secret = uuid.uuid4().hex
    wh = Webhook(
        workspace_id=workspace_id,
        name=name,
        url=url,
        secret=secret,
        event_types=event_types,
    )
    session.add(wh)
    await session.flush()
    return wh


async def fire_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[WebhookDelivery]:
    """Queue a delivery to every Webhook in the workspace subscribed to this
    event_type. Returns delivery rows."""
    webhooks = list(
        (
            await session.execute(
                select(Webhook).where(
                    Webhook.workspace_id == workspace_id,
                    Webhook.is_active.is_(True),
                )
            )
        ).scalars()
    )
    deliveries: list[WebhookDelivery] = []
    now = datetime.now(UTC)
    for wh in webhooks:
        if event_type not in wh.event_types:
            continue
        d = WebhookDelivery(
            webhook_id=wh.id,
            event_type=event_type,
            payload_json=payload,
            status=WebhookDeliveryStatus.pending,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
        )
        session.add(d)
        deliveries.append(d)
    await session.flush()
    return deliveries


async def deliver_pending(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> dict[str, int]:
    """Cron-driven worker: pick up to `limit` pending/failed deliveries whose
    next_attempt_at has passed; HTTP-POST each; update status."""
    now = now or datetime.now(UTC)
    rows = list(
        (
            await session.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.status.in_(
                        [WebhookDeliveryStatus.pending, WebhookDeliveryStatus.failed]
                    )
                )
                .where(WebhookDelivery.next_attempt_at <= now)
                .order_by(WebhookDelivery.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    counts = {"delivered": 0, "failed": 0, "dead_letter": 0}
    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_S) as client:
        for d in rows:
            wh = await session.get(Webhook, d.webhook_id)
            if wh is None or not wh.is_active:
                d.status = WebhookDeliveryStatus.dead_letter
                counts["dead_letter"] += 1
                continue
            try:
                _check_url_at_fire_time(wh.url)
            except ValidationError:
                d.status = WebhookDeliveryStatus.dead_letter
                counts["dead_letter"] += 1
                continue

            payload_bytes = json.dumps(d.payload_json, sort_keys=True).encode("utf-8")
            sig = hmac.new(wh.secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            try:
                resp = await client.post(
                    wh.url,
                    content=payload_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Scryer-Signature": sig,
                        "X-Scryer-Event-Type": d.event_type,
                    },
                )
                d.last_response_status = resp.status_code
                d.last_response_body = resp.text[:1000]
                if 200 <= resp.status_code < 300:
                    d.status = WebhookDeliveryStatus.delivered
                    counts["delivered"] += 1
                else:
                    _schedule_retry(d, now)
                    counts[
                        "failed" if d.status == WebhookDeliveryStatus.failed else "dead_letter"
                    ] += 1
            except httpx.HTTPError as exc:
                d.last_response_body = str(exc)[:1000]
                _schedule_retry(d, now)
                counts["failed" if d.status == WebhookDeliveryStatus.failed else "dead_letter"] += 1
            d.attempts += 1
    await session.flush()
    return counts


def _schedule_retry(d: WebhookDelivery, now: datetime) -> None:
    if d.attempts + 1 >= _MAX_ATTEMPTS:
        d.status = WebhookDeliveryStatus.dead_letter
        d.next_attempt_at = None
    else:
        d.status = WebhookDeliveryStatus.failed
        d.next_attempt_at = now + timedelta(seconds=_BACKOFF_SECONDS[d.attempts + 1])


async def manual_retry(session: AsyncSession, delivery_id: int) -> WebhookDelivery:
    d = await session.get(WebhookDelivery, delivery_id)
    if d is None:
        raise NotFoundError("webhook_delivery", str(delivery_id))
    if d.status not in (WebhookDeliveryStatus.failed, WebhookDeliveryStatus.dead_letter):
        return d
    d.status = WebhookDeliveryStatus.pending
    d.next_attempt_at = datetime.now(UTC)
    await session.flush()
    return d


# ── SSRF defense ────────────────────────────────────────────────────────────


def _validate_url_at_creation(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(f"Webhook URL must be http(s); got {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValidationError("Webhook URL missing hostname")


def _check_url_at_fire_time(url: str) -> None:
    """DNS re-resolve at fire time and reject private IP ranges (RFC 5735 +
    cloud metadata + IPv6 unique-local). Plan §15: rebinding defense."""
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValidationError("URL missing hostname")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ValidationError(f"DNS resolution failed: {exc}") from exc

    for info in infos:
        ip_str = info[4][0]
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValidationError(f"Webhook URL resolves to private/internal IP: {ip}")
