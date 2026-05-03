"""Enable Row Level Security on every multi-tenant data table.

For each table:
  ALTER TABLE x ENABLE ROW LEVEL SECURITY;
  CREATE POLICY x_workspace_isolation ON x
    USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid);

The `, true` second arg to current_setting means "missing GUC returns NULL,
don't error". A NULL setting means "no workspace context" and the comparison
fails — so policy DENIES the row. Fail-closed.

For audit_events (workspace_id nullable for system events) and api_keys /
api_key_usage (workspace_id nullable for user-keyed): the policy also allows
rows where workspace_id IS NULL.

Tables NOT enabled (chicken-and-egg with auth flow):
  workspaces, workspace_members, project_members, users, api_keys,
  service_accounts, invitations, workspace_slugs, project_slugs.
These remain protected by service-layer assert_workspace_member etc.

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
    "usage_records",
    "credentials",
    "projects",
]

# Tables with nullable workspace_id — policy also allows NULL (system rows /
# user-keyed api_keys etc.).
_RLS_TABLES_NULLABLE = [
    "audit_events",
    "api_key_usage",
]


def upgrade() -> None:
    for table in _RLS_TABLES_NOT_NULL:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)"
        )

    for table in _RLS_TABLES_NULLABLE:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id IS NULL OR "
            f"workspace_id = current_setting('app.current_workspace_id', true)::uuid)"
        )


def downgrade() -> None:
    """Disable RLS + drop policies. Reverse order so child tables come first
    (defensive — RLS DISABLE is independent per-table but match upgrade order
    convention)."""
    for table in reversed(_RLS_TABLES_NULLABLE + _RLS_TABLES_NOT_NULL):
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
