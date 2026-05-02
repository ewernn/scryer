"""Sliding-window login rate limiter, in-process.

Per plan §15: brute-force defense without standing up Redis. State is
per-process; multi-worker dynos give each worker its own counter (acceptable
for invite-only beta — swap for Redis when scaling).

Limits are enforced on BOTH email AND IP independently: an attacker rotating
IPs can't probe one email past the cap, and an attacker rotating emails can't
hammer login from one IP past the cap.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from scryer.server.services.errors import PermissionError

_WINDOW_SECONDS = 60
_MAX_ATTEMPTS = 5
_EVICTION_THRESHOLD = 5_000

_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def check_login_rate(*, email: str, ip: str) -> None:
    """Raises PermissionError if either email or ip has hit the cap (5/60s).

    Records an attempt against both keys only after both pass — blocked IPs
    don't burn email attempts and vice versa.
    """
    now = time.monotonic()
    keys = (f"email:{email}", f"ip:{ip}")
    with _lock:
        for key in keys:
            window = _attempts[key]
            while window and window[0] < now - _WINDOW_SECONDS:
                window.popleft()
            if len(window) >= _MAX_ATTEMPTS:
                raise PermissionError(f"Too many login attempts; try again in {_WINDOW_SECONDS}s")
        for key in keys:
            _attempts[key].append(now)
        if len(_attempts) > _EVICTION_THRESHOLD:
            _evict_dead_keys_locked()


def _evict_dead_keys_locked() -> None:
    """Drop keys whose windows have aged out. Caller must hold _lock."""
    dead = [k for k, v in _attempts.items() if not v]
    for k in dead:
        del _attempts[k]
