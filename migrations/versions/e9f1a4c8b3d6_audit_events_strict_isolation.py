"""Audit events: tighten RLS policy to drop the NULL-workspace pass-through.

Original policy from c4f2e1b9a3d5 was:
  USING (workspace_id IS NULL OR workspace_id = current_workspace_id_guc)

The IS NULL clause was meant for "system events that aren't workspace
scoped" (failed logins, cron actions). But every workspace context could
read NULL rows because USING applies per-tenant — a tenant in workspace X
could SELECT from /audit/events and see system events meant to be
operator-only. Information leak.

Audit of write_event callers shows no caller passes workspace_id=None
today (signup, invitations, webhooks, service_accounts all pass an
explicit workspace_id). The IS NULL branch was a footgun waiting for a
caller. Drop it; align audit_events with every other Cat 2 multi-tenant
table.

Side effect: future write_event(workspace_id=None) attempts will fail
the WITH CHECK (USING is the implicit WITH CHECK for INSERT in PG when
no explicit clause is set). Caller MUST pass workspace_id.

If we ever need a true "system audit" stream that's invisible to all
tenants, the right answer is a separate audit_system_events table
queryable only by a privileged role — NOT loose policies on the
tenant-facing audit log.

Revision ID: e9f1a4c8b3d6
Revises: d8a719c5b2e7
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e9f1a4c8b3d6"
down_revision: str | None = "d8a719c5b2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GUC = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_events_workspace_isolation ON audit_events")
    op.execute(
        "CREATE POLICY audit_events_workspace_isolation ON audit_events "
        f"USING (workspace_id = {_GUC})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_events_workspace_isolation ON audit_events")
    op.execute(
        "CREATE POLICY audit_events_workspace_isolation ON audit_events "
        f"USING (workspace_id IS NULL OR workspace_id = {_GUC})"
    )
