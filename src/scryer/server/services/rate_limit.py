"""Simple in-memory rate limiter for the login endpoint.

Per plan §15: brute-force defense without standing up Redis. In-memory
state means it leaks across replicas — acceptable for invite-only beta;
swap for Redis when scaling out.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from scryer.server.services.errors import PermissionError

_WINDOW_SECONDS = 60
_MAX_ATTEMPTS = 5
_attempts: dict[str, deque[float]] = defaultdict(deque)


def check_login_rate(key: str) -> None:
    """Raises PermissionError if `key` (e.g. email + ip) has hit the cap.
    Sliding window: 5 attempts / 60s."""
    now = time.monotonic()
    window = _attempts[key]
    while window and window[0] < now - _WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _MAX_ATTEMPTS:
        raise PermissionError(f"Too many login attempts; try again in {_WINDOW_SECONDS}s")
    window.append(now)
