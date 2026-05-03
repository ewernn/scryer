"""Denormalize workspace_id onto every Cat 2 multi-tenant table for RLS.

For each Cat 2 table:
  - ADD workspace_id (nullable), backfill via JOIN, SET NOT NULL, FK + index.
  - BEFORE INSERT OR UPDATE trigger auto-populates from parent FK row.
  - Trigger also REJECTS explicit workspace_id that doesn't match parent.

Cat 4 (api_keys, api_key_usage):
  - workspace_id stays nullable (NULL = user-keyed, set = SA-keyed).

Revision ID: 99b59febc9c0
Revises: b3a2c1d8e0a4
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "99b59febc9c0"
down_revision: str | None = "b3a2c1d8e0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_workspace_id_not_null(
    table: str,
    backfill_sql: str,
    fk_name: str | None = None,
) -> None:
    """Add workspace_id, backfill, enforce NOT NULL, FK, index."""
    op.add_column(table, sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.execute(backfill_sql)
    op.alter_column(table, "workspace_id", nullable=False)
    op.create_foreign_key(
        fk_name or f"fk_{table}_workspace_id_workspaces",
        table,
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])


def _add_workspace_id_nullable(table: str, fk_name: str | None = None) -> None:
    """Add nullable workspace_id + FK + index (Cat 4)."""
    op.add_column(table, sa.Column("workspace_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        fk_name or f"fk_{table}_workspace_id_workspaces",
        table,
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])


def _drop_workspace_id(table: str, fk_name: str | None = None) -> None:
    op.drop_index(f"ix_{table}_workspace_id", table_name=table)
    op.drop_constraint(
        fk_name or f"fk_{table}_workspace_id_workspaces",
        table,
        type_="foreignkey",
    )
    op.drop_column(table, "workspace_id")


# ---------------------------------------------------------------------------
# Trigger SQL blocks
# ---------------------------------------------------------------------------

# Each trigger function returns NEW after populating/validating workspace_id.
# On INSERT: workspace_id is ignored if supplied (or auto-set if absent).
# On UPDATE of the parent FK col: workspace_id is re-derived.
# On any row: if an explicit workspace_id was supplied and mismatches -> RAISE.
# asyncpg can't run multiple statements in one prepared call. Define this
# blob for readability, then split into one CREATE-FUNCTION per op.execute().

_TRIGGER_FUNCTIONS_SQL_BLOB = """
-- ── 1-hop via project_id ──────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_project()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM projects WHERE id = NEW.project_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on %: supplied % but project % belongs to %',
      TG_TABLE_NAME, NEW.workspace_id, NEW.project_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 1-hop: results.run_id → runs.workspace_id ──────────────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_run()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM runs WHERE id = NEW.run_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on %: supplied % but run % belongs to %',
      TG_TABLE_NAME, NEW.workspace_id, NEW.run_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 1-hop: resource_tags.tag_id → tags.workspace_id ───────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_tag()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM tags WHERE id = NEW.tag_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on resource_tags: supplied % but tag % belongs to %',
      NEW.workspace_id, NEW.tag_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: dataset_records.dataset_id → datasets.workspace_id ─────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_dataset()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM datasets WHERE id = NEW.dataset_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on dataset_records: supplied % but dataset % belongs to %',
      NEW.workspace_id, NEW.dataset_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: agent_tools.agent_id → agents.workspace_id ─────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_agent()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM agents WHERE id = NEW.agent_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on agent_tools: supplied % but agent % belongs to %',
      NEW.workspace_id, NEW.agent_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: suite_tasks.suite_id → suites.workspace_id ─────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_suite()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM suites WHERE id = NEW.suite_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on %: supplied % but suite % belongs to %',
      TG_TABLE_NAME, NEW.workspace_id, NEW.suite_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 3-hop: suite_run_runs.suite_run_id → suite_runs.workspace_id ──────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_suite_run()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM suite_runs WHERE id = NEW.suite_run_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on suite_run_runs: supplied % but suite_run % belongs to %',
      NEW.workspace_id, NEW.suite_run_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: traces.run_id → runs.workspace_id ──────────────────────────────
-- (reuses _trgfn_workspace_from_run — same parent column name)

-- ── 2-hop: trace_steps.trace_id → traces.workspace_id ────────────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_trace()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM traces WHERE id = NEW.trace_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on trace_steps: supplied % but trace % belongs to %',
      NEW.workspace_id, NEW.trace_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: comment_versions.comment_id → comments.workspace_id ────────────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_comment()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM comments WHERE id = NEW.comment_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on comment_versions: supplied % but comment % belongs to %',
      NEW.workspace_id, NEW.comment_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── 2-hop: collection_members.collection_id → collections.workspace_id ─────

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_collection()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM collections WHERE id = NEW.collection_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on collection_members: supplied % but collection % belongs to %',
      NEW.workspace_id, NEW.collection_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;

-- ── Cat 4: api_keys (nullable; SA-keyed sets workspace_id, user-keyed = NULL)

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_service_account_nullable()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  IF NEW.principal_kind = 'service_account' THEN
    SELECT workspace_id INTO STRICT _wid
      FROM service_accounts WHERE id = NEW.principal_service_account_id;
    IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
      RAISE EXCEPTION 'workspace_id mismatch on api_keys: supplied % but SA % belongs to %',
        NEW.workspace_id, NEW.principal_service_account_id, _wid;
    END IF;
    NEW.workspace_id := _wid;
  ELSE
    -- user-keyed: workspace_id must be NULL
    IF NEW.workspace_id IS NOT NULL THEN
      RAISE EXCEPTION 'api_keys with principal_kind=user must have workspace_id = NULL, got %',
        NEW.workspace_id;
    END IF;
    NEW.workspace_id := NULL;
  END IF;
  RETURN NEW;
END;
$$;

-- ── Cat 4: api_key_usage (inherit nullable workspace_id from parent api_key)

CREATE OR REPLACE FUNCTION _trgfn_workspace_from_api_key_nullable()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO _wid FROM api_keys WHERE id = NEW.api_key_id;
  -- allow supplied NULL or matching value; reject mismatch
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id IS DISTINCT FROM _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on api_key_usage: supplied % but api_key % has %',
      NEW.workspace_id, NEW.api_key_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;
"""


# Split blob into individual CREATE FUNCTION statements (asyncpg requirement).
# Each block ends with `$$;` followed by blank line + comment + next CREATE.
def _split_trigger_functions(blob: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for line in blob.splitlines():
        current.append(line)
        if line.strip() == "$$;":
            stmt = "\n".join(current).strip()
            if stmt and "CREATE OR REPLACE FUNCTION" in stmt:
                parts.append(stmt)
            current = []
    return parts


TRIGGER_FUNCTIONS = _split_trigger_functions(_TRIGGER_FUNCTIONS_SQL_BLOB)

DROP_TRIGGER_FUNCTIONS = [
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_project() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_run() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_tag() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_dataset() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_agent() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_suite() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_suite_run() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_trace() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_comment() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_collection() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_service_account_nullable() CASCADE",
    "DROP FUNCTION IF EXISTS _trgfn_workspace_from_api_key_nullable() CASCADE",
]

# (table_name, trigger_name, function_name, fire_on_update_of_col)
# fire_on_update_of_col: the parent FK column; trigger only fires when it changes.
_CAT2_TRIGGERS = [
    # 1-hop via project_id
    ("datasets", "trg_datasets_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("scorers", "trg_scorers_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("agents", "trg_agents_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("tools", "trg_tools_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("prompts", "trg_prompts_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("tasks", "trg_tasks_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("suites", "trg_suites_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("triggers", "trg_triggers_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("comments", "trg_comments_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    ("collections", "trg_collections_workspace_id", "_trgfn_workspace_from_project", "project_id"),
    # 1-hop via run_id
    ("results", "trg_results_workspace_id", "_trgfn_workspace_from_run", "run_id"),
    ("traces", "trg_traces_workspace_id", "_trgfn_workspace_from_run", "run_id"),
    # 1-hop via tag_id
    ("resource_tags", "trg_resource_tags_workspace_id", "_trgfn_workspace_from_tag", "tag_id"),
    # 2-hop: dataset_records
    (
        "dataset_records",
        "trg_dataset_records_workspace_id",
        "_trgfn_workspace_from_dataset",
        "dataset_id",
    ),
    # 2-hop: agent_tools
    ("agent_tools", "trg_agent_tools_workspace_id", "_trgfn_workspace_from_agent", "agent_id"),
    # 2-hop: suite_tasks, suite_runs
    ("suite_tasks", "trg_suite_tasks_workspace_id", "_trgfn_workspace_from_suite", "suite_id"),
    ("suite_runs", "trg_suite_runs_workspace_id", "_trgfn_workspace_from_suite", "suite_id"),
    # 3-hop: suite_run_runs
    (
        "suite_run_runs",
        "trg_suite_run_runs_workspace_id",
        "_trgfn_workspace_from_suite_run",
        "suite_run_id",
    ),
    # 2-hop: trace_steps
    ("trace_steps", "trg_trace_steps_workspace_id", "_trgfn_workspace_from_trace", "trace_id"),
    # 2-hop: comment_versions
    (
        "comment_versions",
        "trg_comment_versions_workspace_id",
        "_trgfn_workspace_from_comment",
        "comment_id",
    ),
    # 2-hop: collection_members
    (
        "collection_members",
        "trg_collection_members_workspace_id",
        "_trgfn_workspace_from_collection",
        "collection_id",
    ),
]

_CAT4_TRIGGERS = [
    (
        "api_keys",
        "trg_api_keys_workspace_id",
        "_trgfn_workspace_from_service_account_nullable",
        "principal_service_account_id",
    ),
    (
        "api_key_usage",
        "trg_api_key_usage_workspace_id",
        "_trgfn_workspace_from_api_key_nullable",
        "api_key_id",
    ),
]


def _create_trigger(table: str, trigger_name: str, fn_name: str, update_col: str) -> None:
    op.execute(f"""
        CREATE TRIGGER {trigger_name}
        BEFORE INSERT OR UPDATE OF {update_col}, workspace_id
        ON {table}
        FOR EACH ROW EXECUTE FUNCTION {fn_name}();
    """)


def _drop_trigger(table: str, trigger_name: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table};")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ── trigger functions ──────────────────────────────────────────────────
    for fn_sql in TRIGGER_FUNCTIONS:
        op.execute(fn_sql)

    # ── Cat 2 tables: 1-hop via project_id ────────────────────────────────
    for table in (
        "datasets",
        "scorers",
        "agents",
        "tools",
        "prompts",
        "tasks",
        "suites",
        "triggers",
        "comments",
        "collections",
    ):
        _add_workspace_id_not_null(
            table,
            f"UPDATE {table} t SET workspace_id = p.workspace_id "
            f"FROM projects p WHERE p.id = t.project_id",
        )

    # ── results: 1-hop via run_id ──────────────────────────────────────────
    _add_workspace_id_not_null(
        "results",
        "UPDATE results t SET workspace_id = r.workspace_id FROM runs r WHERE r.id = t.run_id",
    )

    # ── traces: 1-hop via run_id ───────────────────────────────────────────
    _add_workspace_id_not_null(
        "traces",
        "UPDATE traces t SET workspace_id = r.workspace_id FROM runs r WHERE r.id = t.run_id",
    )

    # ── resource_tags: 1-hop via tag_id ───────────────────────────────────
    _add_workspace_id_not_null(
        "resource_tags",
        "UPDATE resource_tags t SET workspace_id = tg.workspace_id "
        "FROM tags tg WHERE tg.id = t.tag_id",
    )

    # ── dataset_records: 2-hop (datasets already has workspace_id now) ─────
    _add_workspace_id_not_null(
        "dataset_records",
        "UPDATE dataset_records t SET workspace_id = d.workspace_id "
        "FROM datasets d WHERE d.id = t.dataset_id",
    )

    # ── agent_tools: 2-hop (agents already has workspace_id) ──────────────
    _add_workspace_id_not_null(
        "agent_tools",
        "UPDATE agent_tools t SET workspace_id = a.workspace_id "
        "FROM agents a WHERE a.id = t.agent_id",
    )

    # ── suite_tasks, suite_runs: 2-hop (suites already has workspace_id) ──
    _add_workspace_id_not_null(
        "suite_tasks",
        "UPDATE suite_tasks t SET workspace_id = s.workspace_id "
        "FROM suites s WHERE s.id = t.suite_id",
    )
    _add_workspace_id_not_null(
        "suite_runs",
        "UPDATE suite_runs t SET workspace_id = s.workspace_id "
        "FROM suites s WHERE s.id = t.suite_id",
    )

    # ── suite_run_runs: 3-hop (suite_runs already has workspace_id) ────────
    _add_workspace_id_not_null(
        "suite_run_runs",
        "UPDATE suite_run_runs t SET workspace_id = sr.workspace_id "
        "FROM suite_runs sr WHERE sr.id = t.suite_run_id",
    )

    # ── trace_steps: 2-hop (traces already has workspace_id) ──────────────
    _add_workspace_id_not_null(
        "trace_steps",
        "UPDATE trace_steps t SET workspace_id = tr.workspace_id "
        "FROM traces tr WHERE tr.id = t.trace_id",
    )

    # ── comment_versions: 2-hop (comments already has workspace_id) ────────
    _add_workspace_id_not_null(
        "comment_versions",
        "UPDATE comment_versions t SET workspace_id = c.workspace_id "
        "FROM comments c WHERE c.id = t.comment_id",
    )

    # ── collection_members: 2-hop (collections already has workspace_id) ───
    _add_workspace_id_not_null(
        "collection_members",
        "UPDATE collection_members t SET workspace_id = c.workspace_id "
        "FROM collections c WHERE c.id = t.collection_id",
    )

    # ── Cat 4: nullable workspace_id ──────────────────────────────────────
    _add_workspace_id_nullable("api_keys", "fk_api_keys_workspace_id_workspaces")
    # Backfill SA-keyed api_keys
    op.execute("""
        UPDATE api_keys k
        SET workspace_id = sa.workspace_id
        FROM service_accounts sa
        WHERE k.principal_service_account_id = sa.id
          AND k.principal_kind = 'service_account'
    """)

    _add_workspace_id_nullable("api_key_usage", "fk_api_key_usage_workspace_id_workspaces")
    # Backfill from parent api_key
    op.execute("""
        UPDATE api_key_usage u
        SET workspace_id = k.workspace_id
        FROM api_keys k
        WHERE k.id = u.api_key_id
    """)

    # ── Install all triggers ───────────────────────────────────────────────
    for table, trg_name, fn_name, col in _CAT2_TRIGGERS + _CAT4_TRIGGERS:
        _create_trigger(table, trg_name, fn_name, col)


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Drop triggers first
    for table, trg_name, _fn, _col in _CAT2_TRIGGERS + _CAT4_TRIGGERS:
        _drop_trigger(table, trg_name)

    for drop_sql in DROP_TRIGGER_FUNCTIONS:
        op.execute(drop_sql)

    # Drop columns in reverse dependency order (children before parents)
    for table in (
        "api_key_usage",
        "api_keys",
        "collection_members",
        "comment_versions",
        "trace_steps",
        "suite_run_runs",
        "suite_runs",
        "suite_tasks",
        "agent_tools",
        "dataset_records",
        "resource_tags",
        "traces",
        "results",
        "collections",
        "comments",
        "triggers",
        "suites",
        "tasks",
        "prompts",
        "tools",
        "agents",
        "scorers",
        "datasets",
    ):
        _drop_workspace_id(table)
