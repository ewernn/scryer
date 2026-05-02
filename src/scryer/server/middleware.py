"""ASGI middlewares.

- ApiVersionHeadersMiddleware: stamps Scryer-Version + Sunset on every response
  (plan §12 / RFC 8594). Pure ASGI to avoid Starlette BaseHTTPMiddleware's
  body-buffering quirks with streaming responses.
- BodySizeLimitMiddleware: rejects requests whose Content-Length (or actual
  streamed size) exceeds MAX_BODY_BYTES with 413 Payload Too Large.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_VERSION = "2026-05-02"
SUNSET_DATE = "2027-05-02"
DEPRECATION_DOC = "https://scryer.io/docs/migrations/2026-05-02"

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB


class ApiVersionHeadersMiddleware:
    """Adds Scryer-Version, Sunset, and Link (deprecation) headers to every
    HTTP response. Pure ASGI — wraps the `send` callable and injects on
    `http.response.start`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"scryer-version", API_VERSION.encode()))
                headers.append((b"sunset", SUNSET_DATE.encode()))
                headers.append((b"link", f'<{DEPRECATION_DOC}>; rel="deprecation"'.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Rejects oversize request bodies. Two checks:

    1. Fast path: if Content-Length header > max_bytes, send 413 immediately.
    2. Streaming path (chunked transfer, no Content-Length): wrap `receive`
       so each `http.request` chunk is counted; abort with 413 once the
       running total exceeds the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        await _send_413(send, self.max_bytes)
                        return
                except ValueError:
                    pass
                break

        total = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal total, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    rejected = True
                    await _send_413(send, self.max_bytes)
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)


async def _send_413(send: Send, max_bytes: int) -> None:
    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Payload Too Large",
            "status": 413,
            "detail": f"Request body exceeds {max_bytes} bytes",
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
