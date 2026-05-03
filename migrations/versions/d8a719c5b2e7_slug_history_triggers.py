"""Slug history as DB triggers (R2): push the workspace/project slug-history
invariant from convention to structural enforcement.

Before:
  service code (create_workspace, rename_workspace_slug) did dual writes
  — one to the parent table, one to the slug-history table. Forgetting
  either leaves the history inconsistent. Race conditions between concurrent
  renames could leave gaps.

After:
  AFTER INSERT trigger inserts a slug-history row whenever a workspace or
  project is created. AFTER UPDATE trigger inserts a NEW history row +
  retires the OLD one whenever workspace.slug changes. Application code
  just sets the slug; the trigger guarantees history consistency.

  workspace_slugs.slug is the PK, so re-using a retired slug for a different
  workspace fails the uniqueness check inside the trigger and rolls back the
  parent UPDATE — a structural prevention of slug squatting / impersonation.
  Same for projects (project_slugs unique on (workspace_id, slug)).

Note on idempotency: CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS in
upgrade so re-running this migration after a failed half-apply is safe.

Revision ID: d8a719c5b2e7
Revises: c4f2e1b9a3d5
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d8a719c5b2e7"
down_revision: str | None = "c4f2e1b9a3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WORKSPACE_SLUG_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION _trgfn_workspace_slug_history()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO workspace_slugs (slug, workspace_id) VALUES (NEW.slug, NEW.id);
  ELSIF TG_OP = 'UPDATE' AND OLD.slug IS DISTINCT FROM NEW.slug THEN
    INSERT INTO workspace_slugs (slug, workspace_id) VALUES (NEW.slug, NEW.id);
    UPDATE workspace_slugs SET retired_at = now()
      WHERE slug = OLD.slug AND retired_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$;"""

_PROJECT_SLUG_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION _trgfn_project_slug_history()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- project_slugs.id is a separate UUID PK with no PG-side default
  -- (the model uses Python-side default=uuid.uuid4). Trigger inserts
  -- via raw SQL bypass that, so generate the UUID via PG built-in
  -- gen_random_uuid() (PG 13+).
  IF TG_OP = 'INSERT' THEN
    INSERT INTO project_slugs (id, workspace_id, slug, project_id)
    VALUES (gen_random_uuid(), NEW.workspace_id, NEW.slug, NEW.id);
  ELSIF TG_OP = 'UPDATE' AND OLD.slug IS DISTINCT FROM NEW.slug THEN
    INSERT INTO project_slugs (id, workspace_id, slug, project_id)
    VALUES (gen_random_uuid(), NEW.workspace_id, NEW.slug, NEW.id);
    UPDATE project_slugs SET retired_at = now()
      WHERE workspace_id = NEW.workspace_id
        AND slug = OLD.slug
        AND retired_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$;"""


def upgrade() -> None:
    op.execute(_WORKSPACE_SLUG_TRIGGER_FN)
    op.execute(_PROJECT_SLUG_TRIGGER_FN)
    op.execute("DROP TRIGGER IF EXISTS workspace_slug_history ON workspaces")
    op.execute(
        "CREATE TRIGGER workspace_slug_history "
        "AFTER INSERT OR UPDATE OF slug ON workspaces "
        "FOR EACH ROW EXECUTE FUNCTION _trgfn_workspace_slug_history()"
    )
    op.execute("DROP TRIGGER IF EXISTS project_slug_history ON projects")
    op.execute(
        "CREATE TRIGGER project_slug_history "
        "AFTER INSERT OR UPDATE OF slug ON projects "
        "FOR EACH ROW EXECUTE FUNCTION _trgfn_project_slug_history()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS project_slug_history ON projects")
    op.execute("DROP TRIGGER IF EXISTS workspace_slug_history ON workspaces")
    op.execute("DROP FUNCTION IF EXISTS _trgfn_project_slug_history()")
    op.execute("DROP FUNCTION IF EXISTS _trgfn_workspace_slug_history()")
