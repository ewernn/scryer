"""Cascade fixups: extend grandchildren + trigger save/restore include_archived.

Two critic findings from the Wave 2+5 review:

(1) The cascade trigger only fanned out to 1-hop children (projects,
service_accounts, credentials, budgets, webhooks). Versioned resources
(datasets, scorers, agents, tools, prompts, tasks) descend through
projects but the policy USING (workspace_id = guc AND archived_at IS NULL)
checks the row's OWN archived_at — there is no JOIN to projects.archived_at.
So a dataset whose project was archived stayed visible to RLS until its
own archived_at was set. Pre-launch with zero rows this is moot but a
"done proper" policy must close the gap.

Fix: extend _CASCADE_CHILDREN to include the 6 versioned resources
(they all carry workspace_id NOT NULL post-99b59febc9c0, so 1-hop
fan-out by workspace_id works for them too). The cascade still uses
the `WHERE workspace_id = NEW.id AND archived_at IS NULL` guard so it
preserves children that were already independently archived.

(2) PostgreSQL's set_config(name, value, true) is is_local =
TRANSACTION-SCOPED, not trigger-scoped. The Wave 2+5 trigger flips
app.include_archived='true' to make its cascade UPDATEs pass the
post-row USING check, but the GUC stays set for the rest of the
enclosing transaction. Subsequent statements in the same txn (e.g.,
the audit write_event after archive_workspace) silently see archived
rows. This was masked because the only caller (the workspaces.archive
endpoint) explicitly sets include_archived=true via
apply_workspace_context — but any future caller that triggers the
cascade indirectly would inherit the flag.

Fix: wrap the trigger body in save/restore — capture the prior value
of app.include_archived, set 'true' for the cascade, restore the
prior value before RETURN. Idempotent under nested triggers.

Revision ID: 8f52f0aae3fe
Revises: 47ae45c7f465
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8f52f0aae3fe"
down_revision: str | None = "47ae45c7f465"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Children that descend from a workspace by 1-hop workspace_id, mix
# SoftDeleteMixin, and need archived_at fanout. Now includes versioned
# resources (datasets/scorers/etc) — they carry workspace_id NOT NULL
# (denormalized in 99b59febc9c0 via _trgfn_workspace_from_project), so
# `WHERE workspace_id = NEW.id` still works.
_CASCADE_CHILDREN = [
    "projects",
    "service_accounts",
    "credentials",
    "budgets",
    "webhooks",
    "datasets",
    "scorers",
    "agents",
    "tools",
    "prompts",
    "tasks",
]


def upgrade() -> None:
    cascade_updates = "\n  ".join(
        f"UPDATE {tbl} SET archived_at = NEW.archived_at "
        f"WHERE workspace_id = NEW.id AND archived_at IS NULL;"
        for tbl in _CASCADE_CHILDREN
    )
    # Save+restore include_archived so the GUC change doesn't leak past
    # the trigger body. coalesce() returns NULL if the GUC is unset; we
    # round-trip via empty string (the policy NULLIF treats '' as NULL).
    op.execute(
        f"""
CREATE OR REPLACE FUNCTION _trgfn_cascade_archive_workspace()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  _prior text;
BEGIN
  _prior := current_setting('app.include_archived', true);
  PERFORM set_config('app.include_archived', 'true', true);
  {cascade_updates}
  PERFORM set_config('app.include_archived', COALESCE(_prior, ''), true);
  RETURN NEW;
END;
$$;
"""
    )


def downgrade() -> None:
    # Revert to Wave 2+5's narrower cascade: 5 children, no save/restore.
    cascade_updates = "\n  ".join(
        f"UPDATE {tbl} SET archived_at = NEW.archived_at "
        f"WHERE workspace_id = NEW.id AND archived_at IS NULL;"
        for tbl in [
            "projects",
            "service_accounts",
            "credentials",
            "budgets",
            "webhooks",
        ]
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
