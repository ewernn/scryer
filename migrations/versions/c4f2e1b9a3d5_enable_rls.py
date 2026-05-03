"""Enable Row Level Security on every multi-tenant data table.

For each table:
  ALTER TABLE x ENABLE ROW LEVEL SECURITY;
  CREATE POLICY x_workspace_isolation ON x
    USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid);

The `, true` second arg to current_setting means "missing GUC returns NULL,
don't error". A NULL setting means "no workspace context" and the comparison
fails — so policy DENIES the row. Fail-closed.

For audit_events (workspace_id nullable for system events) the policy also
allows rows where workspace_id IS NULL.

This migration also adds workspace_id + a trigger to webhook_deliveries
(the only multi-tenant table that escaped the original denorm migration
99b59febc9c0). Service code can keep emitting NULL on insert; the trigger
copies workspace_id from the parent webhook row.

Tables NOT enabled (intentional Phase 1c scope):

  workspaces, users, invitations, service_accounts, workspace_slugs,
  project_slugs
    — root resolution targets; no parent workspace to gate on.

  workspace_members, project_members
    — service-layer functions (assert_workspace_member,
      assert_workspace_role, assert_project_access,
      list_workspaces_for_user) constrain every query by user_id.
      Adding RLS would require the request flow to inject GUCs
      synchronously before each lookup; the defense-in-depth value
      doesn't justify the per-query gymnastics.

  api_keys
    — the AuthN flow (resolve_api_key in services/api_keys.py) must
      look up by key_prefix BEFORE any workspace context exists. An
      RLS policy that gates on workspace_id breaks SA-keyed AuthN.
      The workspace_id column + CHECK constraints + service-layer
      principal_user_id check provide structural enforcement; RLS
      adds nothing useful here.

  api_key_usage
    — populated by log_api_key_usage which isn't yet wired into the
      request pipeline. Deferred until that wiring lands so we don't
      ship policies guarding nothing.

When those access patterns change (e.g., a new endpoint exposes
workspace_members beyond the service helpers), revisit and add the
missing policies in a follow-up migration.

Rollback: see docs/deployment.md "RLS rollback runbook" — the kill switch
is `ALTER TABLE x DISABLE ROW LEVEL SECURITY;` per table, OR the alembic
downgrade which does it for all.

Revision ID: c4f2e1b9a3d5
Revises: 99b59febc9c0
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c4f2e1b9a3d5"
down_revision: str | None = "99b59febc9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables with NOT NULL workspace_id — straightforward isolation policy.
# webhook_deliveries is appended below in upgrade() once its workspace_id
# column has been added + backfilled.
_RLS_TABLES_NOT_NULL = [
    "datasets",
    "dataset_records",
    "scorers",
    "agents",
    "tools",
    "prompts",
    "tasks",
    "runs",
    "results",
    "traces",
    "trace_steps",
    "suites",
    "suite_tasks",
    "suite_runs",
    "suite_run_runs",
    "triggers",
    "comments",
    "comment_versions",
    "collections",
    "collection_members",
    "agent_tools",
    "resource_tags",
    "webhooks",
    "webhook_deliveries",
    "usage_records",
    "credentials",
    "projects",
]

# Tables with nullable workspace_id — policy also allows NULL (system
# audit events that aren't workspace-scoped, e.g. failed login attempts).
_RLS_TABLES_NULLABLE = [
    "audit_events",
]

# 1-hop trigger function: webhook_deliveries.webhook_id → webhooks.workspace_id.
# Mirrors the pattern from migration 99b59febc9c0 (split per-statement so
# asyncpg's prepared-statement protocol can execute each in isolation).
_WEBHOOK_DELIVERY_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION _trgfn_workspace_from_webhook()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _wid uuid;
BEGIN
  SELECT workspace_id INTO STRICT _wid FROM webhooks WHERE id = NEW.webhook_id;
  IF NEW.workspace_id IS NOT NULL AND NEW.workspace_id <> _wid THEN
    RAISE EXCEPTION 'workspace_id mismatch on webhook_deliveries: supplied % but webhook % belongs to %',
      NEW.workspace_id, NEW.webhook_id, _wid;
  END IF;
  NEW.workspace_id := _wid;
  RETURN NEW;
END;
$$;"""


def upgrade() -> None:
    # ── 1. webhook_deliveries.workspace_id: column + backfill + NOT NULL + trigger ──
    op.execute(
        "ALTER TABLE webhook_deliveries "
        "ADD COLUMN workspace_id uuid REFERENCES workspaces(id) ON DELETE CASCADE"
    )
    op.execute(
        "UPDATE webhook_deliveries d "
        "SET workspace_id = w.workspace_id "
        "FROM webhooks w WHERE d.webhook_id = w.id"
    )
    op.execute("ALTER TABLE webhook_deliveries ALTER COLUMN workspace_id SET NOT NULL")

    op.execute(_WEBHOOK_DELIVERY_TRIGGER_FN)
    op.execute(
        "CREATE TRIGGER set_workspace_id_webhook_deliveries "
        "BEFORE INSERT OR UPDATE ON webhook_deliveries "
        "FOR EACH ROW EXECUTE FUNCTION _trgfn_workspace_from_webhook()"
    )

    # ── 2. ENABLE + FORCE RLS + isolation policies on data tables ──────────
    # FORCE is non-negotiable: without it, the table owner role bypasses RLS
    # entirely (PG default). The application connects as the owner role
    # (`postgres` locally, `neondb_owner` on Neon), so without FORCE RLS is
    # effectively a no-op. The privileged_engine fixture's `scryer_setup`
    # role uses BYPASSRLS to escape FORCE for cross-tenant test setup.
    #
    # NULLIF: an unset GUC returns NULL via current_setting(...,true), but
    # an explicitly-set empty string ('') returns '' which fails the ::uuid
    # cast. NULLIF normalizes both to NULL, which then fails the equality
    # cleanly (= anything yields NULL → false → row denied).
    _guc = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
    for table in _RLS_TABLES_NOT_NULL:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = {_guc})"
        )

    for table in _RLS_TABLES_NULLABLE:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id IS NULL OR workspace_id = {_guc})"
        )


def downgrade() -> None:
    # Reverse-order policy drops + RLS disable. NO FORCE column: DISABLE
    # implicitly clears it.
    for table in reversed(_RLS_TABLES_NULLABLE + _RLS_TABLES_NOT_NULL):
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Webhook_deliveries: drop trigger, drop function, drop column.
    op.execute("DROP TRIGGER IF EXISTS set_workspace_id_webhook_deliveries ON webhook_deliveries")
    op.execute("DROP FUNCTION IF EXISTS _trgfn_workspace_from_webhook()")
    op.execute("ALTER TABLE webhook_deliveries DROP COLUMN IF EXISTS workspace_id")
