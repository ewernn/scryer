"""Typed service-layer errors.

Service functions raise these; the API layer catches and maps to RFC 9457
Problem responses (Phase 1.6+). Never raise HTTPException from a service —
that couples logic to HTTP concerns and breaks CLI/SDK reuse.
"""

from __future__ import annotations


class ScryerError(Exception):
    """Base for all scryer service-layer errors."""


class NotFoundError(ScryerError):
    """A resource was looked up by id/slug and didn't exist."""

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} not found: {identifier}")
        self.resource = resource
        self.identifier = identifier


class ConflictError(ScryerError):
    """A unique-constraint or business-invariant conflict (e.g., slug taken)."""


class PermissionError(ScryerError):  # noqa: A001 — overrides builtin intentionally
    """Caller lacks permission for this action."""


class ValidationError(ScryerError):
    """Input validation failed beyond what Pydantic can express
    (e.g. cross-field invariant)."""


class AuthError(ScryerError):
    """Auth-time failure (bad password, revoked key, expired JWT)."""


class IntegrityError(ScryerError):
    """A multi-step transaction failed an invariant the service is responsible
    for (e.g. polymorphic FK + discriminator mismatch). Should be rare;
    indicates a bug."""
