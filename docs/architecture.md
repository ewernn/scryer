# Architecture

Layered design with hard boundaries: API ↔ services ↔ models ↔ Postgres + R2.

```
api/         <- HTTP boundary, RFC 9457 errors, scope checks
    |
    v
services/    <- pure async business logic, raises typed errors
    |
    v
models/      <- SQLAlchemy 2.x; CHECK constraints; UNIQUE indexes
    |
    v
Postgres (Neon) + R2 (Trajectory spill)
```

## Schema clusters

Models are grouped by cluster, one file each in `src/scryer/server/models/`:

| Cluster | File | Purpose |
|---------|------|---------|
| 1 — Identity | `auth.py` | User, Workspace, Project, ApiKey, Invitation, Credential, ServiceAccount, Membership tables |
| 2 — Eval core | `eval.py` | Dataset, DatasetRecord, Scorer, Agent, Tool, Prompt, Task, Run, Result, Trajectory, TrajectoryStep |
| 3 — Audit + automation | `audit.py` | AuditEvent, Suite, Trigger, UsageRecord, Webhook, WebhookDelivery |
| 4 — Collab | `collab.py` | Comment, CommentVersion, Collection, CollectionMember |

Total ~41 tables. Each `Base.metadata` carries a naming convention so Alembic
autogen produces clean constraint names (`pk_<table>`, `fk_<table>_<col>_<ref>`,
`uq_<table>_<cols>`, `ck_<table>_<name>`, `ix_<table>_<cols>`).

## Versioning

Dataset, Scorer, Agent, Tool, Prompt, Task share `VersionedMixin`:

- `(project_id, slug, version)` UNIQUE
- `parent_id` → previous version (NULL for v1)
- `content_hash` SHA-256 of canonicalized fields
- pushing a new version mints a new immutable row; old rows are kept

## Polymorphic foreign keys

Used in three places (Comments, AuditEvent, ApiKey actor): two nullable
columns (`<kind>_user_id`, `<kind>_service_account_id`) + a `<kind>` enum +
a CHECK constraint enforcing exactly one is non-null. Avoids generic-FK
patterns that break referential integrity.

## Multi-tenancy: workspace_id on every row

Every multi-tenant table carries a `workspace_id` column (NOT NULL FK
CASCADE → workspaces, with a few `nullable=True` exceptions for system
rows like `audit_events` and user-keyed `api_keys`). For tables that
don't naturally hold workspace_id — child rows like `dataset_records`,
`results`, `trajectory_steps` — a PG `BEFORE INSERT OR UPDATE` trigger
auto-populates `NEW.workspace_id` from the parent FK chain
(`_trgfn_workspace_from_project`, `_trgfn_workspace_from_run`, etc.).
Service code never sets `workspace_id` on these tables — the trigger
handles it. The trigger also REJECTS any explicit `workspace_id` that
mismatches the parent (defense in depth).

Workspace context is injected per-request:

1. `require_workspace_from_path` FastAPI dep (in `services/access.py`)
   resolves `{workspace_slug}` from URL → asserts membership → sets
   `request.state.workspace_id` AND `session.info["workspace_id"]`.
   Applied at router level for every router whose paths uniformly
   contain `{workspace_slug}` (audit, projects, datasets, scorers,
   tasks, webhooks, invitations).
2. `db.py:_set_rls_workspace_context` is a SQLAlchemy `after_begin`
   listener on `Session`. At every transaction start, it reads
   `session.info["workspace_id"]` and emits
   `set_config('app.current_workspace_id', ..., true)`. asyncpg can't
   parameterize `SET LOCAL` directly, hence `set_config()` function form.
3. `with_workspace_context(session, workspace_id)` async ctx mgr in
   `db.py` exists for non-HTTP callers (cron jobs, scripts, tests).

Postgres Row Level Security policies gate visibility per
`workspace_id`. RLS is **enabled and FORCEd** on every multi-tenant
table in production (migration `c4f2e1b9a3d5`). The FORCE matters: the
table owner role bypasses RLS by default; without FORCE, RLS is a
no-op for the application's connecting role.

Policies use `NULLIF(current_setting('app.current_workspace_id',
true), '')::uuid` to fail-closed: missing GUC → NULL → equality fails
→ row denied. The `audit_events` policy is stricter (migration
`e9f1a4c8b3d6`) — it requires explicit workspace_id match (no NULL
pass-through), preventing cross-tenant info disclosure on system
events.

Tests run as `scryer_app` (NOT SUPERUSER, NOT BYPASSRLS) so policies
actually apply; cross-tenant test setup uses a `scryer_setup`
(BYPASSRLS) role via `privileged_engine`.

### archived_at as RLS predicate (Wave 5, migration 9db64f8a534b)

For tables that mix `SoftDeleteMixin`, the policy USING also gates
on `archived_at`:

```sql
USING (workspace_id = guc
       AND (archived_at IS NULL OR
            current_setting('app.include_archived', true) = 'true'))
WITH CHECK (workspace_id = guc)
```

Two PG behaviours forced this design:

1. **USING/WITH CHECK split**: PG defaults WITH CHECK to USING when
   omitted. Without the explicit split, every soft-delete UPDATE would
   fail WITH CHECK on the post-row.
2. **Post-row USING**: PG also evaluates SELECT/USING against the
   *new* row of every UPDATE (visibility-after-write check). Setting
   `archived_at = now()` produces a row that fails the USING predicate
   unless `app.include_archived='true'` is set.

The cascade trigger (below) handles #2 by setting `include_archived='true'`
inside its function body and restoring the prior value on RETURN.
Application code that needs to write or view archived rows passes
`include_archived=True` to `apply_workspace_context`.

### Cascade-down soft-delete (Wave 2, migrations 9db64f8a534b + 8f52f0aae3fe)

Archiving a workspace fans out via DB trigger to 11 children with
`workspace_id` NOT NULL: projects, service_accounts, credentials,
budgets, webhooks, datasets, scorers, agents, tools, prompts, tasks.
The trigger fires `AFTER UPDATE OF archived_at WHEN (NEW.archived_at
IS NOT NULL AND OLD.archived_at IS NULL)` — first-archive only,
re-archives are no-ops, un-archives don't cascade-up (Stripe model:
restoration is one-way). Children that are already independently
archived keep their original timestamp via `WHERE archived_at IS NULL`
guard inside the cascade.

`services/workspaces.py:archive_workspace` drives the flow: stamps
the workspace's `archived_at`, lets the trigger cascade, then
bulk-cancels in-flight Runs (`status='cancelled'`). For Runs whose
executor is local (PID matches), it also tears down the subprocess
via the same SIGTERM/grace/SIGKILL sequence as `cancel_run`.

## Auth

`Authorization: Bearer <token>` → `get_principal` Depends inspects prefix:

- `scrk_live_*` → ApiKey path (sha256 hash lookup, scopes from row)
- dot-separated → JWT path (HS256 verify, `sub` = User UUID)

→ `Principal{id, kind, scopes, api_key_id}` is injected into endpoints.
`require_scope("write")` factory wraps for scope enforcement.

`/web/*` uses an httponly cookie (`scryer_session`) holding the same JWT.
Form POSTs validate a CSRF token = HMAC(jwt_secret, session_jwt).

## Errors

Service layer raises typed exceptions:

- `NotFoundError` → 404 (also used to mask permission failures so existence
  isn't leaked via response code)
- `ConflictError` → 409
- `AuthError` → 401
- `PermissionError` → 403
- `ValidationError` → 422
- `IntegrityError` → 409 (DB constraint violations)

Central exception handler maps each to `application/problem+json` per RFC 9457
with a `retryable` extension. Validation errors include per-field details.

## Run executor

`services/runs.py:execute_run`:

1. Atomic `UPDATE ... WHERE status='queued'` claims the Run AND stamps
   `executor_pid = os.getpid()` (concurrency-safe; PID enables Wave 3
   cancellation).
2. Loads Task → Scorer → DatasetRecords.
3. Per-record cooperative cancellation check: `await
   session.refresh(run, ["status"])`; if `cancelled`, break early.
4. For each record, runs the Scorer in a **subprocess sandbox** via
   `sandbox.run_user_code`. The sandbox accepts an `on_proc_start`
   callback; the executor uses it to register the live `Process`
   handle in module-global `_active_procs[run.id] = proc`.
   - stripped env (only PATH, HOME, TMPDIR, PYTHONUNBUFFERED, PYTHONHASHSEED=0)
   - rlimits (CPU, memory, file count, output size)
   - JSON envelope written via side-channel file argv path (avoids stdout pollution)
   - timeout enforced; SIGKILL on overrun
5. Heartbeat by wall-clock every 30 s; reaper marks `failed` after 60 s of silence.
6. Crash-resilient: any non-`SandboxError` exception in the for-loop is
   caught, the Run is stamped `status='failed'` with `executor_crashed: ...`
   reason, and the original exception re-raised. Prevents Runs stuck
   at `status='running'` for the heartbeat reaper to mislabel.
7. On terminal status, fires `run.{status}` event → `services/webhooks.py:fire_event`
   queues one `WebhookDelivery` per subscribed Webhook.
8. Auto-writes a system `Comment` with summary stats (plan §8 layer 3).

`cancel_run` flips `status='cancelled'`. If `executor_pid == os.getpid()`
AND `_active_procs[run_id]` is set, also tears down the subprocess:
`SIGTERM → CANCEL_GRACE_SECONDS (5s) → SIGKILL`. `ProcessLookupError`
on terminate/kill is treated as a benign already-exited race.
Cross-process cancellation lands at the next per-record cooperative
check — at most one record's worth of latency.

Supersession: `queue_run(supersede=True)` atomically marks prior queued/running
Runs of the same Task as `superseded` (FK `superseded_by_run_id` → new Run id;
CHECK `(status='superseded') = (superseded_by_run_id IS NOT NULL)`).

## Idempotency-Key middleware

POST endpoints that mutate state accept the standard `Idempotency-Key`
header and follow Stripe semantics. Implementation:
`services/idempotency.py:check_idempotency` is a FastAPI Depends that
runs after `get_principal` + `require_workspace_from_path`, so RLS +
principal context are established when the lookup happens.

- Header absent → silent no-op (route runs as if no idempotency).
- Same key + same body, prior 2xx/4xx response cached → returns cached
  body via `CachedResponseError` handler with `Idempotent-Replay: true`
  response header.
- Same key + same body, slot still in-flight (status_code IS NULL) → 409.
- Same key + DIFFERENT body → 422 (key reuse with mismatched request).
- 5xx responses NOT cached — let retries proceed.
- 24h TTL (`IDEMPOTENCY_TTL_SECONDS` env var).

Two-tier table: a partial unique index per (`workspace_id`, `principal_id`,
`key`) for workspace-scoped routes and per (`principal_id`, `key`) for
non-workspace routes (both gated by RLS policies).

Wired today on: `POST /workspaces/{slug}/runs`, `POST datasets`, `POST
scorers`, `POST tasks`, `POST webhooks`, `POST service-accounts`, `POST
service-accounts/{id}/api-keys`. Endpoints whose response includes a
one-shot secret (webhook secret, SA `full_key`) pass `cache_body=False`
to `capture_idempotency_response` — replays return the cached
status_code with NULL body, so retries cannot recover the secret.

## Webhook delivery

`services/webhooks.py:deliver_pending` (cron every minute):

1. **Lease pattern**: `SELECT FOR UPDATE SKIP LOCKED` claims a batch, bumps
   `next_attempt_at` by 120 s, COMMITs to release row locks.
2. HTTP POSTs each webhook (signed `X-Scryer-Signature: HMAC-SHA256(secret, body)`).
3. On failure, exponential backoff `[1, 10, 100, 1000]` seconds, floored at
   lease expiry to prevent re-pickup before this worker finishes.
4. Dead-letter after 4 failed attempts.
5. SSRF defense: DNS re-resolution at fire time + RFC 5735 private-range denylist.

## Trajectory storage

`services/trajectories.py:write_trajectory` decides per-Trajectory:

- ≤ 4 KiB serialized → inline TrajectoryStep rows in PG
- larger → one R2 JSON blob at `r2://scryer-blobs/ws/{ws}/proj/{proj}/trajectories/{run}/{record}.json`,
  stored URI + sha256 on the Trajectory row, `storage='r2'`
- CHECK enforces `(storage='inline') OR (storage_uri IS NOT NULL AND storage_sha256 IS NOT NULL)`

R2 PUT happens **before** the PG flush so a failed PUT leaves no dangling
pointer. Orphan blobs (PG flush failed after R2 PUT succeeded) are reaped by
a future GC pass.

## Cold-tier archive (deferred)

PG storage growth is monitored but no Parquet-archive cold tier is implemented.
Build trigger (any one):

1. PG cumulative storage > 50 GB
2. Neon storage line-item > $20/mo
3. User requests > 2 yr audit-log retention

At beta scale (50 Runs/day) the math shows $0/yr savings; at growing scale
(500/day) the ROI is ~5.7 years. See `scryer_notepad.md` 2026-05-02 entry for
the full analysis.

## Audit semantics

Every state-changing service call (`create_*`, `delete_*`, `update_*`,
`rotate_*`) emits an `AuditEvent` via `services/audit.py:write_event`. Sensitive
fields are auto-redacted via `_REDACT_KEYS` (`password`, `password_hash`,
`encrypted_value`, `key_hash`, `token_hash`, `secret`) before the row is
persisted.

## Versioning concurrency

`services/_versioned.py:next_version` takes a transaction-scoped
`pg_advisory_xact_lock` keyed by `hashtext(project_id|slug|table)` before
the SELECT MAX → +1 → INSERT sequence. Concurrent pushes for the same
slug serialize; pushes for different slugs proceed in parallel. The lock
auto-releases at COMMIT/ROLLBACK. Without it, the UNIQUE constraint
catches races with an opaque IntegrityError.

## Scorer return contract

`services/scorer_output.py:ScorerOutput` is the typed Pydantic schema
for what a Scorer's `score()` callable may return. The numeric score
must appear under one of `score`/`value`/`result` (priority order); any
extra fields are allowed and preserved on `Result.score_json`. A Scorer
that returns a dict with no canonical numeric → `Result.error` is set
to "Scorer return schema mismatch" — visible failure rather than the
old silent `score_value=NULL` behavior.

## Test isolation

`conftest.py` spins up a docker postgres container at module load and
runs `alembic upgrade head` against it before any test runs. Each test
gets an `AsyncSession` wrapped in an outer transaction with a SAVEPOINT
that's rolled back on teardown — no commits leak between tests. The
container is reused for the whole pytest session and torn down via
`pytest_sessionfinish` (plus `atexit` belt-and-suspenders).

Because tests run against the migrated schema (not
`Base.metadata.create_all`), triggers, RLS policies, CHECK constraints
— everything PG-specific — are present in the test DB. New migrations
land via `make test-migrations` (separate docker postgres on a
different port) which validates upgrade + downgrade + re-up + smoke
insert/select.

R2 tests gate on `R2_*` env vars (skipped without creds — won't break CI
without R2 access). Tests that exercise the rate limiter call
`_reset_for_tests()` from the `client` fixture so concurrent tests don't
exhaust each other's IP budget from the shared `testclient` host.
