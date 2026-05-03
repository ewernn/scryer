"""Idempotency keys table — per-tier (workspace + non-workspace).

Stripe-style at-least-once → at-most-once via client-supplied
`Idempotency-Key` header. Two unique-index tiers handle the workspace
vs non-workspace route split:

  Workspace-scoped routes:  UNIQUE (workspace_id, principal_id, key)
  Non-workspace routes:     UNIQUE (principal_id, key)  WHERE ws IS NULL

Two RLS policies (AND semantics): workspace isolation + principal
isolation. The latter is defense-in-depth; service-layer also gates
by principal. SA principals don't hit non-workspace routes today, so
the NULL-tier is effectively user-only.

5xx responses are NOT cached (transient — let retries proceed).
4xx responses ARE cached (deterministic).
24h TTL; cleanup belongs to a future cron pass.

Revision ID: a1b2c3d4e5f6
Revises: f3c5d8e0a712
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f3c5d8e0a712"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GUC_WS = "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid"
_GUC_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE idempotency_keys (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    UUID        REFERENCES workspaces(id) ON DELETE CASCADE,
            principal_id    UUID        NOT NULL,
            key             TEXT        NOT NULL,
            request_hash    CHAR(64)    NOT NULL,
            status_code     SMALLINT,
            response_body   JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_idempotency_keys_ws
          ON idempotency_keys (workspace_id, principal_id, key)
          WHERE workspace_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_idempotency_keys_global
          ON idempotency_keys (principal_id, key)
          WHERE workspace_id IS NULL
        """
    )
    op.execute("CREATE INDEX ix_idempotency_keys_expires_at ON idempotency_keys (expires_at)")

    op.execute("ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY idempotency_keys_workspace_isolation ON idempotency_keys "
        f"USING (workspace_id IS NULL OR workspace_id = {_GUC_WS}) "
        f"WITH CHECK (workspace_id IS NULL OR workspace_id = {_GUC_WS})"
    )
    op.execute(
        f"CREATE POLICY idempotency_keys_principal_isolation ON idempotency_keys "
        f"USING (principal_id = {_GUC_USER}) "
        f"WITH CHECK (principal_id = {_GUC_USER})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS idempotency_keys_principal_isolation ON idempotency_keys")
    op.execute("DROP POLICY IF EXISTS idempotency_keys_workspace_isolation ON idempotency_keys")
    op.execute("ALTER TABLE idempotency_keys NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_keys DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS idempotency_keys")
