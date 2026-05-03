"""FROZEN CONTRACT tests for content_hash formulas across VersionedMixin.

Each test hardcodes the expected SHA-256 hex for a known input. If anyone
changes a hash formula (adds a field, removes a field, alters
canonicalization, swaps key order behavior), one of these tests fails
loudly. The hash IS the identity contract — these tests prevent silent
drift the way Git's spec-conformance tests prevent SHA-1 drift.

If you intentionally change a formula, you must:
  1. Re-run the test, get the new expected hex from pytest's diff output.
  2. Update the hardcoded value here.
  3. Author a migration that backfills `content_hash` for existing rows
     under the new formula (or accept version-churn — see notepad
     2026-05-02 entry on content addressing).

DO NOT just rubber-stamp the new value to make CI green. The hash drift
breaks dedup and cross-version references."""

from __future__ import annotations

from scryer.server.services._versioned import content_hash

# ── Scorer ──────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: source_text + server_executable. Soul = source code;
# server_executable IS intrinsic (changes execution semantics: server-side
# subprocess vs client-side / runtime-side handler).


def test_scorer_content_hash_stability() -> None:
    h = content_hash(
        {
            "source_text": "def score(inputs, expected, metadata):\n    return {'score': 1.0}\n",
            "server_executable": True,
        }
    )
    assert h == "927db1bbe321af616bf48e3f15eb76aa98b14635a24aa59bfadd38ee075ae3b2"


# ── Dataset ─────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: schema_json + records. Soul = the data + its expected
# shape. Both are content; record_count is derived (excluded).


def test_dataset_content_hash_stability() -> None:
    h = content_hash(
        {
            "schema_json": {"type": "object", "properties": {"x": {"type": "integer"}}},
            "records": [
                {"record_id": 1, "inputs": {"x": 1}, "expected": None, "metadata_json": {}},
                {"record_id": 2, "inputs": {"x": 2}, "expected": None, "metadata_json": {}},
            ],
        }
    )
    assert h == "c95d3c09cc6df65fa6b19c36a0dd62d4cd245a2b6d97a9842eb0d21c37a81283"


# ── Agent ───────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: source_text + config_json. Same shape as Scorer but
# config_json affects agent behavior (model choice, temperature, etc.).


def test_agent_content_hash_stability() -> None:
    h = content_hash(
        {
            "source_text": "def run(input):\n    return {'output': input}\n",
            "config_json": {"model": "claude-sonnet-4-6", "temperature": 0.0},
        }
    )
    assert h == "dbd46f983a15c25becbecbdeb343601f2068ff63593a77879c69272eb60bd638"


# ── Prompt ──────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: template + template_format.value (string form of enum).
# template_format is intrinsic — it changes how the template is rendered.
# Note: pass enum.value explicitly; raw enum object would TypeError at
# json.dumps.


def test_prompt_content_hash_stability() -> None:
    h = content_hash(
        {
            "template": "Score this output: {{ output }}",
            "template_format": "jinja2",
        }
    )
    assert h == "4db131a2b9ce22cc2dad9c817249879fae0c9631e9f414c4d10d99cf2d6f1955"


# ── Tool ────────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: source_text + schema_json + sandbox_required. Schema
# defines callable signature; sandbox_required is intrinsic security
# property.


def test_tool_content_hash_stability() -> None:
    h = content_hash(
        {
            "source_text": "def calculator(x, y, op):\n    return {'+' : x+y}[op]\n",
            "schema_json": {
                "name": "calculator",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                },
            },
            "sandbox_required": True,
        }
    )
    assert h == "27971e14fe501bcf80d4f9da73b0294a534eb26a34d76e83d330c3bedca7ec4a"


# ── Task ────────────────────────────────────────────────────────────────────
# FROZEN CONTRACT: references-not-content. Hashes the bound child
# (id, version) pairs + params_json. Git-style: a Task is a "commit" that
# pins specific versions of its tree-children. Identity IS those references.
# Changing dataset_version from 2 to 3 is a different Task even if both
# versions of the dataset have the same content_hash.


def test_task_content_hash_stability() -> None:
    h = content_hash(
        {
            "dataset_id": "11111111-1111-1111-1111-111111111111",
            "dataset_version": 1,
            "scorer_id": "22222222-2222-2222-2222-222222222222",
            "scorer_version": 1,
            "agent_id": "33333333-3333-3333-3333-333333333333",
            "agent_version": 1,
            "prompt_id": "44444444-4444-4444-4444-444444444444",
            "prompt_version": 1,
            "params_json": {"max_tokens": 100, "temperature": 0.0},
        }
    )
    assert h == "420174c2043dfa41b41f25558b6a768c955a0505ec65fa1e333d1babf22170b2"


# ── Canonicalization invariant ─────────────────────────────────────────────
# Sorted keys + compact separators must produce the same hash regardless of
# input dict ordering. Lock this in as well so a future "remove sort_keys
# for performance" attempt fails loudly.


def test_content_hash_is_key_order_independent() -> None:
    a = content_hash({"alpha": 1, "beta": 2, "gamma": 3})
    b = content_hash({"gamma": 3, "alpha": 1, "beta": 2})
    c = content_hash({"beta": 2, "gamma": 3, "alpha": 1})
    assert a == b == c


def test_content_hash_uses_compact_separators() -> None:
    """No whitespace between keys/values. Locks in separators=(',', ':')."""
    # If the formula ever drifted to default json.dumps spacing, the hash
    # would differ from this hardcoded value.
    h = content_hash({"a": 1, "b": 2})
    assert h == "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
