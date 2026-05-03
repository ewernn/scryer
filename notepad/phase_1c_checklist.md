# Phase 1c — ENABLE RLS shipping checklist

The migration is drafted at `migrations/draft/c4f2e1b9a3d5_enable_rls.py`
and validated end-to-end via `make test-migrations` (upgrade → smoke →
downgrade → re-up). 4 things must land BEFORE moving it to
`migrations/versions/`. Total: ~3-4h focused work.

## 1. db.py listener extension — read `current_user_id`

Currently `_set_rls_workspace_context` reads `session.info["workspace_id"]`
and emits `set_config('app.current_workspace_id', ..., true)`. Add a
parallel read for `current_user_id`:

```python
@event.listens_for(Session, "after_begin")
def _set_rls_context(session, transaction, connection):
    ws_id = session.info.get("workspace_id")
    if ws_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_workspace_id', :wid, true)"),
            {"wid": str(ws_id)},
        )
    user_id = session.info.get("current_user_id")
    if user_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
```

Update `with_workspace_context` to also accept `user_id` (or add a
sibling `with_user_context`). Update `require_workspace_from_path` in
`services/access.py` to set BOTH:

```python
session.info["workspace_id"] = ws.id
session.info["current_user_id"] = principal.id  # NEW
request.state.workspace_id = ws.id
```

For routes WITHOUT workspace context (auth, me) but WITH a principal,
set just `current_user_id`. Add a `set_user_context_dep` that
`get_principal` callers can use.

Test: extend `tests/test_rls_listener.py` to verify both GUCs are set
when both info keys are populated.

## 2. RLS policies on auth tables

Three tables currently SKIPPED in the draft migration. Add to upgrade:

```sql
ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY workspace_members_user_isolation ON workspace_members
  USING (user_id = current_setting('app.current_user_id', true)::uuid);

ALTER TABLE project_members ENABLE ROW LEVEL SECURITY;
CREATE POLICY project_members_user_isolation ON project_members
  USING (user_id = current_setting('app.current_user_id', true)::uuid);

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY api_keys_workspace_or_user ON api_keys
  USING (
    workspace_id IS NULL  -- user-keyed: visible only via user_id below
    OR workspace_id = current_setting('app.current_workspace_id', true)::uuid
  );
```

Note: `workspaces` itself stays exempt — it's the root resolution
target. Membership check happens against `workspace_members` via
`current_user_id` policy.

`api_keys` policy is "workspace_id matches OR is NULL" — for user-keyed
keys (NULL workspace), the principal_user_id check happens in
`resolve_api_key` service; RLS is defense-in-depth, not the primary
gate for user keys.

Test: add a test that proves a User can SELECT their own
workspace_members rows but NOT another user's.

## 3. `privileged_engine` fixture for cross-tenant test setup

Some tests genuinely need to insert data across workspaces (squatter
+ redeemed personal workspace; cross-user IDOR tests). Add to
`tests/conftest.py`:

```python
@pytest_asyncio.fixture(scope="session")
async def privileged_engine() -> AsyncIterator[AsyncEngine]:
    """Bypasses RLS — for cross-tenant test data setup ONLY.
    Connects as scryer_setup role (BYPASSRLS attribute).
    Never use this for assertion paths — those should run under
    real RLS via `engine` + `workspace_context()`."""
    # ...
```

One-time setup against the docker postgres:

```sql
CREATE ROLE scryer_setup LOGIN PASSWORD 'test';
ALTER ROLE scryer_setup BYPASSRLS;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO scryer_setup;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO scryer_setup;
```

Embed this in the docker postgres init via a `docker-entrypoint-initdb.d`
script OR run it inline in `_start_docker_pg` after `pg_isready`.

Verified: Neon Free tier allows this (`neondb_owner` inherits BYPASSRLS
from `neon_superuser`; can grant to custom roles via `ALTER ROLE`).

Tests requiring `privileged_engine` (per the test-wrap agent's report):

- `tests/test_invitations_service.py::test_redeem_survives_personal_workspace_slug_collision`
  (squatter workspace + redeemed personal workspace = two tenants)
- `tests/test_invitations_service.py::test_redeem_invitation_creates_user_membership_personal_ws_default_project`
  (inviter's workspace + invitee's personal workspace = two tenants)
- `tests/test_workspaces_service.py::test_list_workspaces_for_user`
  (split into per-workspace assertion blocks)
- `tests/test_api_keys_service.py::*` user-keyed tests
  (Cat 4 NULL workspace_id, no workspace to wrap)

## 4. Move + verify

```bash
mv migrations/draft/c4f2e1b9a3d5_enable_rls.py migrations/versions/
cd ~/code/scryer
make test-migrations    # MANDATORY — must pass
make test               # full suite, not just individual tests
                        # (known docker flakiness in full runs;
                        # if a test fails, retry or investigate)
```

Once all green:

```bash
git add -A
git commit -m "Phase 1c: ENABLE RLS on every multi-tenant table

[detailed message describing what was done in items 1-4]"
git push
```

Verify Railway deploy succeeds:

```bash
sleep 90
curl https://scryer-production.up.railway.app/api/v1/healthz
# expect: {"status":"ok","db_ok":true}
```

## Rollback

If anything goes wrong post-deploy:

```bash
~/.local/bin/uv run alembic downgrade -1   # reverts the RLS migration
git push origin main --force-with-lease    # ONLY with user approval
```

Or per-table kill switch:

```sql
DROP POLICY IF EXISTS <table>_workspace_isolation ON <table>;
ALTER TABLE <table> DISABLE ROW LEVEL SECURITY;
```

Full runbook in `docs/deployment.md` ("RLS rollback runbook").

## Detection signals

Once RLS is on, the failure mode is silent zero-row results. Watch for:

- New endpoint returns empty list/404 in prod after deploy (was
  populated before)
- Sentry captures NotFoundError that wasn't there before
- `/healthz/deep` (when D3 lands as part of Wave Q) fails the canary
  "I can SELECT a row inside a known workspace context" check

## Definition of done

- [ ] Item 1: db.py listener extended; tests pass
- [ ] Item 2: 3 auth-table RLS policies added to draft migration
- [ ] Item 3: privileged_engine fixture working; cross-tenant tests pass
- [ ] Item 4: migration moved to versions/; `make test-migrations` ✓;
      `make test` ✓ (full suite); pushed; prod healthy
- [ ] scryer_notepad.md updated: prod head migration is now
      `c4f2e1b9a3d5`; "Active wave" pointer changes to next wave
- [ ] This file moved to `notepad/done/` or deleted
