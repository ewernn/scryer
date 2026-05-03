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
Postgres (Neon) + R2 (Trace spill)
```

## Schema clusters

Models are grouped by cluster, one file each in `src/scryer/server/models/`:

| Cluster | File | Purpose |
|---------|------|---------|
| 1 — Identity | `auth.py` | User, Workspace, Project, ApiKey, Invitation, Credential, ServiceAccount, Membership tables |
| 2 — Eval core | `eval.py` | Dataset, DatasetRecord, Scorer, Agent, Tool, Prompt, Task, Run, Result, Trace, TraceStep |
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
`results`, `trace_steps` — a PG `BEFORE INSERT OR UPDATE` trigger
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

Postgres Row Level Security policies (one per multi-tenant table) gate
visibility to the row's `workspace_id`. RLS is currently NOT yet enabled
in production — the migration is parked in `migrations/draft/` pending
auth-flow extensions for `current_user_id`. See
`scryer_notepad.md` for the Phase 1c plan.

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

1. Atomic `UPDATE ... WHERE status='queued'` claims the Run (concurrency-safe).
2. Loads Task → Scorer → DatasetRecords.
3. For each record, runs the Scorer in a **subprocess sandbox**:
   - stripped env (only PATH, HOME, TMPDIR, PYTHONUNBUFFERED, PYTHONHASHSEED=0)
   - rlimits (CPU, memory, file count, output size)
   - JSON envelope written via side-channel file argv path (avoids stdout pollution)
   - timeout enforced; SIGKILL on overrun
4. Heartbeat by wall-clock every 30 s; reaper marks `failed` after 60 s of silence.
5. On terminal status, fires `run.{status}` event → `services/webhooks.py:fire_event`
   queues one `WebhookDelivery` per subscribed Webhook.
6. Auto-writes a system `Comment` with summary stats (plan §8 layer 3).

Supersession: `queue_run(supersede=True)` atomically marks prior queued/running
Runs of the same Task as `superseded` (FK `superseded_by_run_id` → new Run id;
CHECK `(status='superseded') = (superseded_by_run_id IS NOT NULL)`).

## Webhook delivery

`services/webhooks.py:deliver_pending` (cron every minute):

1. **Lease pattern**: `SELECT FOR UPDATE SKIP LOCKED` claims a batch, bumps
   `next_attempt_at` by 120 s, COMMITs to release row locks.
2. HTTP POSTs each webhook (signed `X-Scryer-Signature: HMAC-SHA256(secret, body)`).
3. On failure, exponential backoff `[1, 10, 100, 1000]` seconds, floored at
   lease expiry to prevent re-pickup before this worker finishes.
4. Dead-letter after 4 failed attempts.
5. SSRF defense: DNS re-resolution at fire time + RFC 5735 private-range denylist.

## Trace storage

`services/traces.py:write_trace` decides per-Trace:

- ≤ 4 KiB serialized → inline TraceStep rows in PG
- larger → one R2 JSON blob at `r2://scryer-blobs/ws/{ws}/proj/{proj}/traces/{run}/{record}.json`,
  stored URI + sha256 on the Trace row, `storage='r2'`
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
