## 2026-05-03 — Cleanup execution, session 2: pre-launch redesign push

After cold-tier deferral + Waves 0-6/7/8 landed (session 1 above),
we ran the no-shortcuts redesign per the may2_scryer_plan revision.
**16 commits this session, 159 tests passing, prod healthy.**

### What landed (in order)

1. **B0 + B1** (`21aee27`): invitations savepoint rollback fix; explicit
   DB pool config (`db_pool_size=5, max_overflow=5, timeout=30s,
   recycle=1800s` for Neon `-pooler` in transaction mode).

2. **P1 Phase 1a** (`dad34d3`): RLS session listener via SQLAlchemy
   `after_begin` event on `Session`. Reads `session.info["workspace_id"]`,
   emits `set_config('app.current_workspace_id', :wid, true)`. asyncpg
   can't parameterize `SET LOCAL`, hence the `set_config` function
   form. `with_workspace_context(session, ws_id)` async ctx mgr in
   `db.py` for non-HTTP callers. 4 tests.

3. **P2** (`55611ac`): `tests/test_content_hash_stability.py` —
   hardcoded SHA-256 hex per VersionedMixin user (Scorer, Dataset,
   Agent, Prompt, Tool, Task) + canonicalization invariants. FROZEN
   CONTRACT docstrings on each `content_hash` call site. Locks in dedup
   identity formula.

4. **D4 + P1 Phase 1b** (`cc06011`): **Migration CI infrastructure** +
   denormalize `workspace_id` to ~20 multi-tenant tables.
   - `scripts/test_migration.sh` + `scripts/smoke_migration.py` —
     docker postgres → `alembic upgrade head` → ORM smoke insert+select
     → `downgrade base` → `upgrade head` → smoke again. `make
     test-migrations` invokes it. Caught the asyncpg
     "cannot insert multiple commands" bug immediately on the denorm
     migration's trigger function blob.
   - Migration `99b59febc9c0`: 12 PL/pgSQL trigger functions
     (`_trgfn_workspace_from_project|run|tag|dataset|agent|suite|...`)
     auto-populate workspace_id from parent FK chain on
     INSERT/UPDATE. Trigger also REJECTS explicit workspace_id that
     mismatches parent. Backfill JOIN through FK chain (no-op
     pre-launch). Cat 4 (api_keys, api_key_usage) gets nullable
     workspace_id with `ondelete=SET NULL`.

5. **P1 Phase 3** (`d886383`): **Test fixture refactor** — switched
   `tests/conftest.py` from Neon-schema-isolation + `create_all` to
   docker postgres + `alembic upgrade head`. Tests now see real
   triggers + CHECKs + RLS-ready schema. **10× faster** (17s vs 3+
   min). `_PG_CONTAINER = scryer_pytest_<port>` so multiple pytest
   invocations on different ports don't collide. `migrations/env.py`
   honors `SCRYER_TEST_DATABASE_URL` env var so subprocess alembic
   doesn't pin prod URL via `get_settings` lru_cache.

6. **P1 model changes** (`1c86a52`): `workspace_id: Mapped[uuid.UUID]`
   added to ~20 ORM models (VersionedMixin in base.py, plus
   DatasetRecord, AgentTool, Result, Trace, TraceStep in eval.py,
   Suite/SuiteTask/SuiteRun/SuiteRunRun/Trigger/ResourceTag in
   audit.py, Comment/CommentVersion/Collection/CollectionMember in
   collab.py, ApiKey/ApiKeyUsage nullable in auth.py). Service code
   doesn't pass workspace_id on insert — trigger handles it. Also
   fixes `test_listener_skips_when_info_empty` for PG 17 (returns
   None not "" for unset GUC).

7. **P1 Phase 5** (`19157e6`): `require_workspace_from_path` FastAPI
   Depends in `services/access.py`. Resolves `{workspace_slug}` from
   URL → `assert_workspace_member` → sets `request.state.workspace_id`
   AND `session.info["workspace_id"]`. Applied at router level via
   `dependencies=[Depends(require_workspace_from_path)]` on every
   workspace-scoped router (audit, projects, datasets, scorers,
   tasks, webhooks, invitations). Today is structurally a no-op (RLS
   not enabled); once Phase 1c lands, this is the structural
   guarantee that handlers can't forget to set context.

8. **R3 + C3** (`d167e8d`): `pg_advisory_xact_lock(hashtext(project|
   slug|table))` in `next_version` — concurrent pushes for the same
   slug serialize, different slugs parallel. C3:
   `SUNSET_DATE`/`DEPRECATION_DOC` derived from `API_VERSION` (single
   source of truth).

9. **C1 + workspace_context test wrappings** (`a82f14f`):
   `services/scorer_output.py:ScorerOutput` Pydantic model + 9 tests.
   Replaces `_coerce_score`'s silent-NULL bug — wrong-keyed return
   now stores explicit error in `Result.error`. Plus agent-driven
   pass adding `async with workspace_context(session, ws.id):`
   wrappings to ~12 service-test files (35 wrappings, no-op today).

10. **Phase 1c WIP + rollback runbook** (`ac28d8c`): ENABLE RLS
    migration drafted at `migrations/draft/c4f2e1b9a3d5_enable_rls.py`.
    Validated end-to-end via `make test-migrations` (upgrade →
    smoke → downgrade → re-up: all pass). NOT YET in `versions/`
    because applying broke 7 auth-flow tests. `docs/deployment.md`
    gained the RLS rollback runbook (kill switch + detection
    signals + defensive checks).

11. **docs refresh** (`ad0cde7`): `docs/architecture.md` gained
    sections on Multi-tenancy (denorm + triggers + Depends + listener
    + Phase 1c status), Versioning concurrency (R3), Scorer return
    contract (C1). Test isolation section rewritten for
    docker-postgres reality. `docs/main.md` repo-layout comment
    refreshed.

### Phase 1c shipping checklist (4 items, ~3-4h focused work)

This is the foundation for tenant isolation; it deserves its own
session. Migration is drafted and validated; what's blocking is auth
flow + test refactor:

1. **`db.py` listener extension**: read `session.info["current_user_id"]`,
   emit `set_config('app.current_user_id', :uid, true)` in addition
   to workspace_id. Required for workspace_members RLS policy.
2. **3 RLS policies on auth tables** (currently SKIPPED in draft):
   - `workspace_members USING (user_id = current_setting('app.current_user_id', true)::uuid)`
   - `project_members USING (user_id = ...)` same shape
   - `api_keys USING (workspace_id IS NULL OR workspace_id = current_setting('app.current_workspace_id', true)::uuid)`
3. **`privileged_engine` fixture in conftest** — dedicated BYPASSRLS
   role (Neon Free tier supports this; verified via investigator).
   `CREATE ROLE scryer_setup LOGIN PASSWORD '...'; ALTER ROLE
   scryer_setup BYPASSRLS;` (executed once by `neondb_owner`). Tests
   needing it (per the test-wrap agent's report):
   `test_redeem_survives_personal_workspace_slug_collision`,
   `test_redeem_invitation_creates_user_membership_personal_ws_default_project`,
   `test_list_workspaces_for_user`, user-keyed api_key tests
   (Cat 4 NULL workspace_id, no ws to wrap with).
4. **Move `migrations/draft/c4f2e1b9a3d5_enable_rls.py` →
   `migrations/versions/`**. Run `make test-migrations` then `make
   test` (full suite, not just individual tests — there's known
   intermittent docker container flakiness in full-suite runs).

### Remaining queue (post-Phase 1c)

```
□ Phase 1c — bundle (above 4 items)
□ P1 Phase 2  — webhooks lease refactor (use privileged session
                for cron workers — they're inherently cross-tenant)
□ P1 Phase 4  — cascade-down soft-delete + idempotency_keys table
                (must be RLS-aware from day 1)
□ R1 — audit auto-emission via SQLAlchemy after_insert/update/delete
       on a Auditable mixin (eliminates per-service write_event
       discipline; audit holes structurally impossible)
□ R2 — slug history as DB trigger
       (workspaces.slug docstring already calls this a deferred
       shortcut)
□ R4 — finish ServiceAccount as first-class principal (build, not
       remove — ~8 model fields + access.py raises + audit
       discriminator currently dangling)
□ P3 — outbox table only (Procrastinate deferred until ≥3 job types)
□ Q  — cursor pagination on every list endpoint (~6-8h),
       /healthz/deep, drop Sunset header (no real versioning behind)
```

### Known non-blocking issues to address eventually

- **Test suite flakiness in full runs**: tests pass individually but
  full suite occasionally fails with docker connection errors.
  Pre-existing (this session inherited it). Workaround: re-run.
  Investigate: increase docker startup wait? connection pool
  exhaustion? asyncpg state leak across sessions?
- **Trigger functions live in `public` schema**: should be
  `scryer_internal.` for namespacing. Defer to Phase 1c-2 or later.
- **WebhookDelivery no workspace_id**: critic flagged this gap when
  Phase 1c lands (deliveries become only Cat2 table without it).
  Fix as part of Phase 1c migration: add to denorm + trigger from
  webhook_id parent.

### Test count history this session

106 (start) → 124 (Wave 6 close, session 1) → 134 (Wave 7) → 139 (Wave
4 cleanup) → 150 (P3 conftest) → 159 (current). All real new tests
plus the test-wrap agent adding workspace_context to 12 files.

### Operational state at session close

- Live: <https://scryer-production.up.railway.app/api/v1/healthz>
  (`db_ok: true` after every commit — Railway auto-deploys main)
- Migration head in prod: `99b59febc9c0` (denorm). `c4f2e1b9a3d5`
  (RLS enable) is parked in `migrations/draft/`, NOT applied.
- 159 tests pass, 17-20s with docker postgres reuse.
- `make test-migrations` works end-to-end; gates every future
  migration before it can ship.

