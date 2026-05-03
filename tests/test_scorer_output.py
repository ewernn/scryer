"""parse_scorer_output: replaces the silent-NULL bug from _coerce_score.

A Scorer returning {"score": 0.9} → 0.9, no error.
A Scorer returning {"score_value": 0.9} (wrong key!) → None, error message.
Previously the latter would silently store score_value=NULL with no signal."""

from __future__ import annotations

from scryer.server.services.scorer_output import parse_scorer_output


def test_canonical_score_key() -> None:
    val, err = parse_scorer_output({"score": 0.9})
    assert val == 0.9
    assert err is None


def test_value_alias() -> None:
    val, err = parse_scorer_output({"value": 0.5, "reasoning": "..."})
    assert val == 0.5
    assert err is None


def test_result_alias() -> None:
    val, err = parse_scorer_output({"result": 1.0})
    assert val == 1.0
    assert err is None


def test_priority_score_over_value() -> None:
    val, err = parse_scorer_output({"score": 0.7, "value": 0.3, "result": 0.1})
    assert val == 0.7  # `score` wins per priority order
    assert err is None


def test_extra_fields_allowed() -> None:
    val, err = parse_scorer_output({"score": 0.5, "breakdown": {"a": 1, "b": 2}})
    assert val == 0.5
    assert err is None


def test_silent_null_bug_now_explicit() -> None:
    """Regression: returning a wrong-keyed dict used to silently store NULL.
    Now returns an explicit error message."""
    val, err = parse_scorer_output({"score_value": 0.9})  # wrong key
    assert val is None
    assert err is not None
    assert "schema mismatch" in err.lower()


def test_non_dict_return_handled() -> None:
    val, err = parse_scorer_output(0.5)  # bare float
    assert val is None
    assert err is not None
    assert "expected dict" in err.lower()


def test_empty_dict_rejected() -> None:
    val, err = parse_scorer_output({})
    assert val is None
    assert err is not None


def test_dict_with_only_extras_rejected() -> None:
    val, err = parse_scorer_output({"reasoning": "great", "confidence": 0.8})
    assert val is None
    assert err is not None
