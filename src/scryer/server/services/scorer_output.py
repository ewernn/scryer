"""Typed schema for what a Scorer's `score()` callable may return.

Replaces the old `_coerce_score` key-list scan (`("score", "value", "result")`)
which silently stored `score_value=NULL` if the Scorer returned a dict whose
numeric value was under a different key (e.g. `score_value`, `pass`, etc.).

Now: validate against `ScorerOutput`. If the dict has a numeric in any of
the canonical fields → store. If not → log a clear error + store
`error="Scorer return schema mismatch"` so the failure is visible, not
silent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class ScorerOutput(BaseModel):
    """Canonical Scorer return shape. The numeric score MUST appear under
    one of `score` / `value` / `result` (in priority order). Any extra
    fields are allowed (reasoning, breakdown, citations, etc.) — they are
    preserved on the Result row's score_json column."""

    model_config = ConfigDict(extra="allow")

    score: float | None = None
    value: float | None = None
    result: float | None = None

    @model_validator(mode="after")
    def at_least_one_numeric(self) -> ScorerOutput:
        if self.score is None and self.value is None and self.result is None:
            raise ValueError(
                "Scorer return must include a numeric value under one of: "
                "'score', 'value', or 'result'"
            )
        return self

    def coerce_score(self) -> float:
        """Return the canonical score. Priority: score → value → result.
        Validator guarantees at least one is non-None."""
        for v in (self.score, self.value, self.result):
            if v is not None:
                return v
        raise AssertionError("validator should have rejected — at least one must be set")


def parse_scorer_output(payload: Any) -> tuple[float | None, str | None]:
    """Parse a raw Scorer output dict. Returns (score_value, error_message).

    Pre-launch contract: if the Scorer returned anything but a dict matching
    ScorerOutput, the result row gets score_value=None and a clear error.
    Callers store both — the run's overall pass/fail counts are based on
    error presence, not score_value semantics."""
    if not isinstance(payload, dict):
        return None, f"Scorer return was {type(payload).__name__}, expected dict"
    try:
        out = ScorerOutput.model_validate(payload)
    except ValueError as exc:
        return None, f"Scorer return schema mismatch: {exc}"
    return out.coerce_score(), None
