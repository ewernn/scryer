"""Time-as-DB-truth: stamp created_at / updated_at via server-side now().

Push timestamps from Python (per-process clock) to PostgreSQL (single
source of truth). Multi-process workers (web + cron + future executor)
can have skewed clocks — Railway containers don't share an NTP source.
A 30-second skew between the cron worker and the running web process
silently kills healthy runs (`reap_stale_runs` compares Python-time on
cron-worker to Python-time-frozen-on-the-running-worker's heartbeat).

This migration adds `DEFAULT now()` to every TimestampMixin column. New
INSERTs that don't supply created_at/updated_at get the DB clock.
Existing rows are unaffected. The Python `default=utc_now` stays in the
ORM for tests/scripts that bypass server-side defaults — both layers
agree.

Revision ID: f3c5d8e0a712
Revises: e9f1a4c8b3d6
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f3c5d8e0a712"
down_revision: str | None = "e9f1a4c8b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# All TimestampMixin tables. Tables NOT in this list either don't have
# TimestampMixin (audit_events, api_key_usage, dataset_records,
# trace_steps, results, project_slugs, workspace_slugs) or use their own
# timestamp pattern (Run.queued_at / .started_at — set explicitly by the
# state machine, not at insert).
# Tables that mix in TimestampMixin (verified by grep against models/).
# Tables NOT here: agent_tools, suite_tasks, suite_run_runs, comment_versions,
# resource_tags, usage_records, collection_members — they use no TimestampMixin
# (or use bespoke timestamp columns).
_TIMESTAMPED_TABLES = [
    "users",
    "workspaces",
    "workspace_members",
    "projects",
    "project_members",
    "service_accounts",
    "api_keys",
    "invitations",
    "credentials",
    "share_grants",
    "budgets",
    "datasets",
    "scorers",
    "agents",
    "tools",
    "prompts",
    "tasks",
    "runs",
    "traces",
    "suites",
    "suite_runs",
    "triggers",
    "webhooks",
    "tags",
    "comments",
    "collections",
]


def upgrade() -> None:
    for table in _TIMESTAMPED_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at SET DEFAULT now()")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN updated_at SET DEFAULT now()")


def downgrade() -> None:
    for table in _TIMESTAMPED_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN created_at DROP DEFAULT")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN updated_at DROP DEFAULT")
