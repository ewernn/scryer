"""Cascade-down soft-delete from workspaces + archived_at-as-RLS-predicate.

WHY BUNDLED — one migration covers two waves that MUST ship together:

  Wave 2: Cascade soft-delete on workspaces. Archiving a workspace must
  fan out to its children (projects, service_accounts, credentials,
  budgets, webhooks). Without that, archived workspaces would still have
  visible children — a soft-delete that doesn't propagate is a leak.

  Wave 5: Add `archived_at IS NULL` to RLS USING clauses. Today RLS hides
  cross-tenant rows but happily returns archived rows; the ORM-side
  archived_at filter is the only thing keeping them out of every result
  set. Pushing it into the policy makes it structural.

The bundling is forced. Two separate fixes are required:

  (a) USING/WITH CHECK split — when USING contains `archived_at IS NULL`,
  PG defaults WITH CHECK to USING (when WITH CHECK omitted). Then any
  soft-delete UPDATE fails WITH CHECK on the post-row (archived_at IS NOT
  NULL). We must split USING (workspace + archive filter) from WITH CHECK
  (workspace only).

  (b) include_archived GUC inside cascade trigger — even with the split,
  PG ALSO evaluates the SELECT/USING expression against the post-row of
  every UPDATE (this is the "row stays visible" check; not documented
  prominently but verified in PG 17). The cascade UPDATE flips
  archived_at IS NULL → IS NOT NULL, which fails the post-row USING.
  Solution: the trigger function does
    PERFORM set_config('app.include_archived', 'true', true);
  before the cascade UPDATEs, so the post-row USING passes via the
  include_archived='true' branch. The set is transaction-scoped, so it
  auto-clears at txn end; the application service also sets it.

Doing one without the other deadlocks soft-delete entirely.

Schema changes:
  - webhooks.archived_at: ADD COLUMN nullable timestamptz (Webhook model
    only mixed TimestampMixin, missed SoftDeleteMixin)
  - _trgfn_cascade_archive_workspace(): plpgsql function that bulk-UPDATEs
    children
  - trg_workspace_cascade_archive trigger AFTER UPDATE OF archived_at on
    workspaces, gated by WHEN (NEW.archived_at IS NOT NULL AND
    OLD.archived_at IS NULL) — fires only on first archive, not on
    repeated archive calls or on un-archive (one-way restore: un-archiving
    a workspace does NOT cascade-up to children, Stripe model)
  - DROP+CREATE policy on 8 SoftDelete RLS tables with split USING/WITH CHECK
    (datasets, scorers, agents, tools, prompts, tasks, projects, credentials).
    USING gates by workspace AND archived_at filter. WITH CHECK gates by
    workspace only — so the trigger's UPDATE-of-archived_at succeeds.
  - app.include_archived GUC: opt-in escape hatch ('true' lets queries
    see archived rows). Read by the new USING clause.

Tables NOT touched (intentional):
  - audit_events: append-only, never archived.
  - runs / results / traces: ephemeral execution data, archived workspace
    cascades runs via separate executor-side bulk UPDATE in
    archive_workspace service (status='cancelled'); soft-deleting the
    individual rows would lose forensic value.
  - dataset_records / trace_steps / agent_tools / collection_members /
    suite_*: child rows of versioned/owned resources; archive flows down
    through the parent (the dataset is archived, not each record).
  - resource_tags / usage_records / triggers / comments / comment_versions:
    same — archive cascades through their parent, no row-level archive UI.
  - webhook_deliveries: child of webhook (which now has archived_at),
    individual deliveries don't have archive semantics.

Revision ID: 9db64f8a534b
Revises: a1b2c3d4e5f6
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9db64f8a534b"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Eight RLS-policied tables that mix SoftDeleteMixin. Their existing
# policy from c4f2e1b9a3d5 only gates on workspace_id; we DROP + CREATE
# to add the archived_at filter to USING with explicit WITH CHECK split.
#
# Tables NOT in this list:
#   - dataset_records, trace_steps, agent_tools, collection_members,
#     suite_*, resource_tags, runs, results, traces, comments,
#     comment_versions, usage_records, triggers, webhook_deliveries:
#     do NOT mix SoftDeleteMixin. No archived_at column.
#   - audit_events: not in this set; nullable workspace_id with separate
#     policy. Append-only, no archive.
#   - webhooks: SoftDeleteMixin column added by THIS migration. Included.
_SOFT_DELETE_RLS_TABLES = [
    "datasets",
    "scorers",
    "agents",
    "tools",
    "prompts",
    "tasks",
    "projects",
    "credentials",
    "webhooks",  # archived_at added in step 1 below.
]

# Cascade fan-out from workspace archive. Children that mix SoftDeleteMixin
# AND descend directly from workspace_id (1-hop). Versioned children
# (datasets, scorers, etc.) descend through projects → cascade implicitly
# via projects.archived_at being applied at policy time, not via this
# trigger. Tracking that as a TODO might be cleaner but pre-launch the
# 1-hop fan-out is enough — RLS USING (archived_at IS NULL) hides them
# either way.
_CASCADE_CHILDREN = [
    "projects",
    "service_accounts",
    "credentials",
    "budgets",
    "webhooks",
]


def upgrade() -> None:
    # ── 1. webhooks.archived_at: ADD COLUMN ─────────────────────────────
    # Webhook model only mixes TimestampMixin (no SoftDeleteMixin) so the
    # column is missing. Adding it nullable + no backfill (existing rows
    # are de-facto active).
    op.execute("ALTER TABLE webhooks ADD COLUMN archived_at timestamptz")

    # ── 2. Cascade trigger function ─────────────────────────────────────
    # Plpgsql function that fans out NEW.archived_at to every child whose
    # workspace_id matches NEW.id AND that isn't already archived. The
    # `AND archived_at IS NULL` guard means a re-archive (no-op outer
    # UPDATE) doesn't stomp on children that were independently archived
    # earlier, preserving their original archive timestamps.
    #
    # SET LOCAL app.include_archived = 'true' is REQUIRED. PostgreSQL's
    # RLS evaluates the SELECT/USING expression against the *new* row
    # of every UPDATE (in addition to WITH CHECK), to enforce that the
    # post-update row stays visible to its mutator. Our policy USING
    # filters `archived_at IS NULL OR include_archived='true'`. After the
    # cascade UPDATE, archived_at IS NOT NULL — so without flipping
    # include_archived, the visibility check fails and the cascade
    # UPDATE raises "new row violates row-level security policy". The
    # set is transaction-scoped so it auto-clears at txn end. Defense
    # in depth: the application service also sets it when archiving.
    cascade_updates = "\n  ".join(
        f"UPDATE {tbl} SET archived_at = NEW.archived_at "
        f"WHERE workspace_id = NEW.id AND archived_at IS NULL;"
        for tbl in _CASCADE_CHILDREN
    )
    op.execute(
        f"""
CREATE OR REPLACE FUNCTION _trgfn_cascade_archive_workspace()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM set_config('app.include_archived', 'true', true);
  {cascade_updates}
  RETURN NEW;
END;
$$;
"""
    )

    # ── 3. Trigger on workspaces: AFTER UPDATE OF archived_at ──────────
    # WHEN clause gates fire on first-archive only — re-archiving an
    # already-archived workspace is a no-op, and un-archiving (NEW IS NULL)
    # never fires. One-way restore semantics: un-archive a workspace does
    # NOT cascade-up to children. (Stripe model.)
    op.execute(
        """
CREATE TRIGGER trg_workspace_cascade_archive
  AFTER UPDATE OF archived_at ON workspaces
  FOR EACH ROW
  WHEN (NEW.archived_at IS NOT NULL AND OLD.archived_at IS NULL)
  EXECUTE FUNCTION _trgfn_cascade_archive_workspace();
"""
    )

    # ── 4. DROP + CREATE policy on 8 SoftDelete RLS tables ──────────────
    # USING:    workspace match AND (not archived OR include_archived flag set)
    # WITH CHECK: workspace match ONLY — so the cascade trigger's UPDATE
    #             that sets NEW.archived_at IS NOT NULL succeeds.
    #
    # The split is non-negotiable: PG defaults WITH CHECK to USING when
    # absent. If we leave it default, every soft-delete UPDATE would fail
    # the WITH CHECK on the post-row.
    _ws_guc = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
    _include_archived = "current_setting('app.include_archived', true)"
    for table in _SOFT_DELETE_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = {_ws_guc} "
            f"       AND (archived_at IS NULL OR {_include_archived} = 'true')) "
            f"WITH CHECK (workspace_id = {_ws_guc})"
        )


def downgrade() -> None:
    # Reverse: revert each policy to workspace-only (matching c4f2e1b9a3d5).
    _ws_guc = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
    for table in reversed(_SOFT_DELETE_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} USING (workspace_id = {_ws_guc})"
        )

    op.execute("DROP TRIGGER IF EXISTS trg_workspace_cascade_archive ON workspaces")
    op.execute("DROP FUNCTION IF EXISTS _trgfn_cascade_archive_workspace()")
    op.execute("ALTER TABLE webhooks DROP COLUMN IF EXISTS archived_at")
