"""Rename Trace → Trajectory across the schema.

Pre-launch decision: "Trajectory" is the more accurate term for what
this resource captures (a sequence of agent/tool/result steps for a
single record). We rename now while there are zero rows in production
and no API consumers depending on the OpenAPI shape.

Touched DB objects:

  Tables:
    traces           → trajectories
    trace_steps      → trajectory_steps

  Columns:
    trajectory_steps.trace_id → trajectory_id

  Constraints (PG ALTER TABLE RENAME doesn't auto-rename):
    ck_traces_r2_has_uri_and_checksum   → ck_trajectories_*
    fk_traces_run_id_runs               → fk_trajectories_run_id_runs
    fk_traces_workspace_id_workspaces   → fk_trajectories_workspace_id_workspaces
    pk_traces                           → pk_trajectories
    uq_traces_run_record                → uq_trajectories_run_record
    fk_trace_steps_tool_id_tools        → fk_trajectory_steps_tool_id_tools
    fk_trace_steps_trace_id_traces      → fk_trajectory_steps_trajectory_id_trajectories
    fk_trace_steps_workspace_id_workspaces → fk_trajectory_steps_workspace_id_workspaces
    pk_trace_steps                      → pk_trajectory_steps

  Indexes (most auto-rename with their constraint, but explicit ones don't):
    ix_traces_workspace_id              → ix_trajectories_workspace_id
    ix_trace_steps_tool_id              → ix_trajectory_steps_tool_id
    ix_trace_steps_trace_id_seq         → ix_trajectory_steps_trajectory_id_seq
    ix_trace_steps_workspace_id         → ix_trajectory_steps_workspace_id

  Triggers:
    trg_traces_workspace_id             → trg_trajectories_workspace_id
    trg_trace_steps_workspace_id        → trg_trajectory_steps_workspace_id

  Trigger function (rename + body rewrite):
    _trgfn_workspace_from_trace         → _trgfn_workspace_from_trajectory

  RLS policies:
    traces_workspace_isolation          → trajectories_workspace_isolation
    trace_steps_workspace_isolation     → trajectory_steps_workspace_isolation

R2 path scheme also flips: kind="traces" → kind="trajectories" in
services/blobs.py allowed-list. Pre-launch = no blobs to migrate.

Revision ID: c557d6a52788
Revises: 8f52f0aae3fe
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c557d6a52788"
down_revision: str | None = "8f52f0aae3fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Rename tables ────────────────────────────────────────────────
    op.execute("ALTER TABLE traces RENAME TO trajectories")
    op.execute("ALTER TABLE trace_steps RENAME TO trajectory_steps")

    # ── 2. Rename FK column on the steps table ──────────────────────────
    op.execute("ALTER TABLE trajectory_steps RENAME COLUMN trace_id TO trajectory_id")

    # ── 3. Rename constraints ──────────────────────────────────────────
    # PG renames PRIMARY KEY indexes alongside their constraint, so the
    # ALTER TABLE RENAME CONSTRAINT updates both.
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "ck_traces_r2_has_uri_and_checksum TO ck_trajectories_r2_has_uri_and_checksum"
    )
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "fk_traces_run_id_runs TO fk_trajectories_run_id_runs"
    )
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "fk_traces_workspace_id_workspaces TO fk_trajectories_workspace_id_workspaces"
    )
    op.execute("ALTER TABLE trajectories RENAME CONSTRAINT pk_traces TO pk_trajectories")
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "uq_traces_run_record TO uq_trajectories_run_record"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trace_steps_tool_id_tools TO fk_trajectory_steps_tool_id_tools"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trace_steps_trace_id_traces TO "
        "fk_trajectory_steps_trajectory_id_trajectories"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trace_steps_workspace_id_workspaces TO "
        "fk_trajectory_steps_workspace_id_workspaces"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT pk_trace_steps TO pk_trajectory_steps"
    )

    # ── 4. Rename non-PK indexes (ALTER INDEX RENAME) ──────────────────
    op.execute("ALTER INDEX ix_traces_workspace_id RENAME TO ix_trajectories_workspace_id")
    op.execute("ALTER INDEX ix_trace_steps_tool_id RENAME TO ix_trajectory_steps_tool_id")
    op.execute(
        "ALTER INDEX ix_trace_steps_trace_id_seq RENAME TO ix_trajectory_steps_trajectory_id_seq"
    )
    op.execute("ALTER INDEX ix_trace_steps_workspace_id RENAME TO ix_trajectory_steps_workspace_id")
    # PK indexes named pk_trace_steps / pk_traces auto-followed their
    # constraints in step 3 above (PG renames PK index alongside).

    # ── 5. Rename triggers ─────────────────────────────────────────────
    op.execute(
        "ALTER TRIGGER trg_traces_workspace_id ON trajectories "
        "RENAME TO trg_trajectories_workspace_id"
    )
    op.execute(
        "ALTER TRIGGER trg_trace_steps_workspace_id ON trajectory_steps "
        "RENAME TO trg_trajectory_steps_workspace_id"
    )

    # ── 6. Rename trigger function + rewrite its body ──────────────────
    # The function references "traces" by name in its SELECT and in
    # its error message; both must be updated. CREATE OR REPLACE under
    # the new name, then drop the old. (Renaming alone via ALTER
    # FUNCTION RENAME doesn't update the body.)
    op.execute("DROP FUNCTION IF EXISTS _trgfn_workspace_from_trace() CASCADE")
    op.execute(
        """
CREATE OR REPLACE FUNCTION _trgfn_workspace_from_trajectory()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM trajectories WHERE id = NEW.trajectory_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on trajectory_steps: supplied % but trajectory % belongs to %',
      NEW.workspace_id, NEW.trajectory_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;
"""
    )
    # The DROP ... CASCADE above also dropped the trigger that called
    # the old function. Recreate trigger pointing at the new function.
    op.execute(
        "CREATE TRIGGER trg_trajectory_steps_workspace_id "
        "BEFORE INSERT OR UPDATE ON trajectory_steps "
        "FOR EACH ROW EXECUTE FUNCTION _trgfn_workspace_from_trajectory()"
    )

    # ── 7. Rename RLS policies (PG: DROP + CREATE; no ALTER POLICY rename) ──
    _ws_guc = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
    op.execute("DROP POLICY IF EXISTS traces_workspace_isolation ON trajectories")
    op.execute("DROP POLICY IF EXISTS trace_steps_workspace_isolation ON trajectory_steps")
    op.execute(
        f"CREATE POLICY trajectories_workspace_isolation ON trajectories "
        f"USING (workspace_id = {_ws_guc})"
    )
    op.execute(
        f"CREATE POLICY trajectory_steps_workspace_isolation ON trajectory_steps "
        f"USING (workspace_id = {_ws_guc})"
    )


def downgrade() -> None:
    # Reverse everything in inverse order. Symmetric.
    _ws_guc = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"

    op.execute("DROP POLICY IF EXISTS trajectories_workspace_isolation ON trajectories")
    op.execute("DROP POLICY IF EXISTS trajectory_steps_workspace_isolation ON trajectory_steps")
    op.execute(
        f"CREATE POLICY traces_workspace_isolation ON trajectories USING (workspace_id = {_ws_guc})"
    )
    op.execute(
        f"CREATE POLICY trace_steps_workspace_isolation ON trajectory_steps "
        f"USING (workspace_id = {_ws_guc})"
    )

    # Function: drop the new (CASCADE drops its trigger) and recreate the old + trigger.
    op.execute("DROP FUNCTION IF EXISTS _trgfn_workspace_from_trajectory() CASCADE")
    op.execute(
        """
CREATE OR REPLACE FUNCTION _trgfn_workspace_from_trace()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM trajectories WHERE id = NEW.trajectory_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on trace_steps: supplied % but trace % belongs to %',
      NEW.workspace_id, NEW.trajectory_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;
"""
    )
    op.execute(
        "CREATE TRIGGER trg_trace_steps_workspace_id "
        "BEFORE INSERT OR UPDATE ON trajectory_steps "
        "FOR EACH ROW EXECUTE FUNCTION _trgfn_workspace_from_trace()"
    )

    # Triggers
    op.execute(
        "ALTER TRIGGER trg_trajectories_workspace_id ON trajectories "
        "RENAME TO trg_traces_workspace_id"
    )

    # Indexes
    op.execute("ALTER INDEX ix_trajectories_workspace_id RENAME TO ix_traces_workspace_id")
    op.execute("ALTER INDEX ix_trajectory_steps_tool_id RENAME TO ix_trace_steps_tool_id")
    op.execute(
        "ALTER INDEX ix_trajectory_steps_trajectory_id_seq RENAME TO ix_trace_steps_trace_id_seq"
    )
    op.execute("ALTER INDEX ix_trajectory_steps_workspace_id RENAME TO ix_trace_steps_workspace_id")

    # Constraints
    op.execute("ALTER TABLE trajectories RENAME CONSTRAINT pk_trajectories TO pk_traces")
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "uq_trajectories_run_record TO uq_traces_run_record"
    )
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "fk_trajectories_workspace_id_workspaces TO fk_traces_workspace_id_workspaces"
    )
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "fk_trajectories_run_id_runs TO fk_traces_run_id_runs"
    )
    op.execute(
        "ALTER TABLE trajectories RENAME CONSTRAINT "
        "ck_trajectories_r2_has_uri_and_checksum TO ck_traces_r2_has_uri_and_checksum"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT pk_trajectory_steps TO pk_trace_steps"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trajectory_steps_workspace_id_workspaces TO fk_trace_steps_workspace_id_workspaces"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trajectory_steps_trajectory_id_trajectories TO fk_trace_steps_trace_id_traces"
    )
    op.execute(
        "ALTER TABLE trajectory_steps RENAME CONSTRAINT "
        "fk_trajectory_steps_tool_id_tools TO fk_trace_steps_tool_id_tools"
    )

    # Column
    op.execute("ALTER TABLE trajectory_steps RENAME COLUMN trajectory_id TO trace_id")

    # Tables
    op.execute("ALTER TABLE trajectory_steps RENAME TO trace_steps")
    op.execute("ALTER TABLE trajectories RENAME TO traces")
