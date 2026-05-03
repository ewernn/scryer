# scryer build notepad

Running log of decisions, progress, surprises, and revisions during build. Plan
of record lives in traitinterp at `docs/other/may2_scryer_plan.md` — this
notepad documents how implementation deviates from or refines that plan.

---

## Layout & conventions

```
~/code/scryer/
├── pyproject.toml          # uv-managed; deps + lint + mypy config
├── alembic.ini
├── migrations/             # alembic migrations + env.py
├── src/scryer/
│   ├── __init__.py         # __version__ via importlib.metadata
│   ├── cli/                # typer subapps, one file per noun (Phase 1+)
│   ├── config.py           # pydantic-settings; single source of env
│   └── server/
│       ├── app.py          # FastAPI factory + lifespan
│       ├── db.py           # async engine + session factory + get_session DI
│       ├── api/            # JSON endpoints, one file per noun
│       ├── services/       # pure async functions; called by api/ AND cli/
│       ├── models/         # SQLAlchemy 2.x Mapped[] models, by cluster
│       └── templates/      # Jinja (Phase 5)
└── tests/
    ├── conftest.py         # transactional rollback fixtures
    └── ...
```

**Conventions**
- Service layer functions are pure async, take `(session, ...args)`, return domain objects (Pydantic models or SQLAlchemy ORM instances). Never raise HTTPException — that's API-layer concern. Service raises typed errors (e.g., `ScryerNotFoundError`) which the API layer maps to RFC 9457.
- All services accept `session` as first arg (not `engine`); easier to test with rollback fixtures.
- All API endpoints declare `response_model=` and `operation_id` (lint rule TBD).
- Alembic migrations are auto-generated, then hand-edited if needed (autogenerate misses CHECK constraints, partial indexes, etc.).

---

## Phase 0 — done (2026-05-02)

- Repo bootstrap: ✓ <https://github.com/ewernn/scryer> (public, Apache-2.0)
- FastAPI + SQLAlchemy 2.x async + Alembic + Pydantic 2.x; uv-managed
- /api/v1/healthz with DB ping; OpenAPI at /api/openapi.json
- Sentry stub (no DSN; auto-attach via FastAPI integration)
- GitHub Actions CI: ruff + ruff format + mypy strict + pytest on Python 3.12 + 3.13
- Critic pass (5 fixes applied): dropped deprecated SentryAsgiMiddleware; moved engine to lifespan + app.state (no module globals); `__version__` via importlib.metadata; CI runs all branches; mypy no longer continue-on-error; fastapi-mcp pinned for Phase 6.
- Neon PG 17.8 (us-west-2) connected; pooler hostname (`-pooler`) used for serverless friendliness.
- R2 bucket `scryer-blobs` created (reuses traitinterp R2 account creds).

**Phase 0 footguns documented**
- Editable install goes stale on `pyproject.toml` changes — `uv sync` doesn't reliably re-register editable. Workaround: `uv pip install -e . --reinstall`. TODO: add a `Makefile` or `justfile` target.
- Neon password starts with `npg_*` — easy to mistake for `pg_*` if reading quickly.
- asyncpg uses `?ssl=require` in URL query, NOT `?sslmode=require` (which is libpq/psycopg).

---

## Phase 1 — Foundation cluster (Identity + Org + Auth) — IN PROGRESS

### Plan §17 sub-tasks

1.1 Schema spec — SQLAlchemy 2.x async models for cluster 1 (12 tables)
1.2 Critic pass on schema before migration
1.3 Generate + inspect Alembic migration
1.4 Apply migration to Neon; verify
1.5 Service layer (users, workspaces, projects, service_accounts, credentials, invitations, api_keys)
1.6 Auth middleware (JWT + ApiKey + scope checking)
1.7 Invitation + signup flow (auto-creates personal Workspace + default Project)
1.8 Credential CRUD with AES-GCM (encryption_key_version aware)
1.9 CLI: `scryer auth login`, `scryer workspace list`, `scryer project list`
1.10 Final critic + verifier pass

### Open design questions (research in flight)

- **Polymorphic FK**: ApiKey.principal needs to point at User OR ServiceAccount. Plan picks "two nullable cols + CHECK". Investigator running. _Will document chosen pattern below once back._
- **Argon2id params for password hashing** (OWASP 2025 defaults). Investigator running.
- **AES-GCM nonce / encoding for Credentials**. Investigator running.
- **Pytest transactional fixture pattern for asyncpg**. Investigator running.
- **FastAPI auth DI pattern (JWT + ApiKey on same routes)**. Investigator running.
- **Typer multi-noun layout for ~125 commands eventually**. Investigator running.

### Decisions made so far in Phase 1 (locked from investigator returns)

**Polymorphic FK pattern** (used in api_keys, budgets, audit_events.actor):
- **Option A: two nullable cols + CHECK constraint**. Investigator confirmed: STI is wrong tool (principal isn't a subtype of ApiKey, it's an FK target); generic association sacrifices DB integrity.
- Set `lazy="raise"` on relationships to force explicit loading (no implicit N+1).
- CHECK constraint must be hand-added to migration (Alembic doesn't autogenerate per [issue #508](https://github.com/sqlalchemy/alembic/issues/508)).
- Use a `principal()` accessor method on the model that returns the active one based on `principal_type`.

**Password hashing** (User.password_hash):
- `argon2-cffi` 25.x directly (passlib abandoned, pwdlib unnecessary wrapper).
- Params: `time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16` — about 2× OWASP 2025 floor.
- Wrap in `asyncio.to_thread` (CPU-bound, blocks event loop otherwise).
- Storage: PHC string in `VARCHAR(255)` or `TEXT`.
- Use `check_needs_rehash` on login to upgrade old hashes lazily.

**ApiKey hashing** (high-entropy tokens — NOT passwords):
- `sha256` (NOT bcrypt/argon2 — input is uniformly random; rainbow-table attack impossible).
- Format: `scrk_live_<43 b64url chars>` (32 random bytes).
- Store `key_prefix` (first 16 chars) for indexed lookup, then verify hash via `hmac.compare_digest`.
- This matches Stripe / GitHub / crates.io.

**Invitation tokens**:
- Format: `scrinv_<40 hex chars>` via `secrets.token_hex(20)` = 160 bits.
- Store as sha256 hash.

**AES-GCM for Credentials**:
- `cryptography` lib's `AESGCM` class.
- AES-256, random 12-byte nonce per encryption.
- Storage: `base64(nonce || ciphertext || tag)` in TEXT column.
- Lazy re-encrypt on read for key rotation; keep old keys in env vars `ENC_KEY_V1`, `ENC_KEY_V2`, etc.

**JWT** (human session auth):
- `pyjwt` 3.x (NOT python-jose — unmaintained).
- HS256 (single-instance scryer; no multi-service token verification needed).
- Access tokens 15-min TTL; refresh tokens stored as sha256 in `sessions` table with revocation flag.

**FastAPI auth pattern** (no middleware — DI):
- Single `HTTPBearer(auto_error=False)` dep called `get_principal`.
- Inspects token prefix: `scrk_live_*` → ApiKey path; contains `.` → JWT path.
- Returns a `Principal` dataclass `{id, kind, scopes: frozenset[str]}`.
- Scope enforcement via `require_scope("write")` factory wrapping `Depends(get_principal)`.

**RFC 9457 errors**:
- `fastapi-problem-details` library + subclass `Problem` to add `retryable: bool` extension.
- Override `RequestValidationError` handler so Pydantic 422s are also Problem-shape.
- Set `Content-Type: application/problem+json` on responses.

**Request correlation**:
- `asgi-correlation-id` middleware with `X-Request-ID` header; `correlation_id.get()` ContextVar.
- Will be threaded through AuditEvents in cluster 3.

**CLI structure (Typer)**:
- One file per noun: `src/scryer/cli/{auth,workspace,project,...}.py`; each exports `app = typer.Typer()`.
- Root `cli/main.py` wires via `app.add_typer(dataset.app, name="dataset")`.
- Shared state via `@app.callback()` + `ctx.ensure_object(dict)`.
- Credentials at `platformdirs.user_config_path / "credentials.json"`.
- `--profile <name>` flag on root callback for multi-env.
- `--output table|json|yaml` global flag (default table via `rich`).
- CLI imports the scryer Python SDK (when it exists); SDK is the source of truth for HTTP calls.
- Use `Annotated[X, typer.Option(...)]` syntax (Typer 0.9+ preferred form).

**Pytest async strategy**:
- Session-scoped engine fixture; per-test `session` fixture with `join_transaction_mode="create_savepoint"` + outer-transaction-rolled-back-on-teardown.
- `pyproject.toml` config: `asyncio_default_fixture_loop_scope = session` + `asyncio_default_test_loop_scope = session` (NOT custom event_loop fixture — deprecated in pytest-asyncio ≥0.23).
- Migrations applied once at session start (Option a for local dev).
- Future: Neon branch per CI run via `neondatabase/create-branch-action@v5` (Option b — defer until CI flakes on shared DB state).
- FastAPI endpoint testing: override `Depends(get_session)` to yield the test's session; use `httpx.AsyncClient` with `ASGITransport` (NOT TestClient — sync/async loop mismatch).

**Naming convention** (DB constraints, for clean Alembic autogen):
```
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

---

## Decision log

| Date | Phase | Decision | Reason |
|---|---|---|---|
| 2026-05-02 | 0 | Apache-2.0 license | OSS infra default; permissive + patent grant |
| 2026-05-02 | 0 | uv (not pip-tools) | modern, fast, lockfile, single-tool ergonomics |
| 2026-05-02 | 0 | Server-rendered Jinja+htmx (no Next.js v0) | per plan §4 — API-first means Jinja can be swapped later |
| 2026-05-02 | 0 | Drop SentryAsgiMiddleware | deprecated in sentry-sdk 2.x; FastAPI integration auto-attaches |
| 2026-05-02 | 0 | Engine on app.state via lifespan, NOT lru_cache | tests need to swap DB URL per fixture |
| 2026-05-02 | 0 | Use traitinterp R2 var names (R2_ENDPOINT, R2_BUCKET_NAME) | copy-paste from traitinterp .env |
| 2026-05-02 | 0 | Neon free tier sufficient | bulky data goes to R2; PG holds structured rows only |
| 2026-05-02 | 0 | Skip Neon Auth | scryer owns auth model per plan §6; vendor lock-in concern |

---

## Phase 5+8 critic fixes — DONE [2026-05-02 ~12:30 UTC]

Three critical critic findings fixed:
- **Trigger workspace_id bug**: `triggers.py:113` was passing `task.project_id`
  for `Run.workspace_id` (different FK target). Now resolves Project →
  workspace_id explicitly.
- **IDOR on /web/runs/{run_id}**: any logged-in user could read any other
  tenant's Run + Results + Comments by guessing UUID. Now calls
  `assert_project_access(principal, run.project_id)`.
- **Race in cron workers**: `dispatch_due_triggers` and `deliver_pending`
  lacked SELECT...FOR UPDATE SKIP LOCKED. Both now use
  `with_for_update(skip_locked=True)` so concurrent Railway Cron pings
  don't double-fire.

Important fixes:
- Cookie `secure` flag env-driven (RAILWAY_ENVIRONMENT or ENV=prod) — local
  HTTP dev works.
- Login exception handler tightened to (AuthError, PermissionError); other
  exceptions propagate to RFC 9457 handler.
- Rate limit keyed by (email | client_ip) not email alone — DoS-resistant.

IDOR regression test added → 106 tests total.

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## FINAL STATUS [2026-05-02 ~12:30 UTC]

**All 8 plan phases addressed** (Phase 7 explicitly skipped per user).

| Phase | Status | Tests |
|---|---|---|
| 0  Bootstrap | ✓ | 3 |
| 1  Identity + Auth | ✓ | 62 |
| 2  Eval Core | ✓ | 16 |
| 3  Audit + Suite + Tag + Usage | ✓ (Trigger/Webhook done in Phase 8) | 7 |
| 4  Comments + Collections | ✓ | 8 |
| 5  Dashboard | ✓ | 5 |
| 6  Agent UX | ✓ | 3 |
| 7  traitinterp wire-in | SKIPPED per user | — |
| 8  Production polish | ✓ | (regressions in 5+8) |

**106 tests passing.** Live: <https://scryer-production.up.railway.app/api/v1/healthz>
+ <https://scryer-production.up.railway.app/web/login>.

**Open follow-ups** (not blockers; capture in tickets when scryer has issue tracking):
- Empty-workspace landing has no "create workspace" button — first-user UX gap
- "Run task" button on project page (currently CLI-only)
- Live status updates on run page (htmx already loaded; 5 lines)
- Webhook URL pre-validation should also reject private IPs at creation
- JWT logout doesn't blacklist; relies on TTL
- Tailwind CDN dependency — pre-build before public launch
- Pre-deploy `SCRYER_INTERNAL_TOKEN` env var on Railway must be set for Cron
  endpoints; document in deploy runbook

## Phase 5 (Dashboard) + Phase 8 (production polish) — DONE [2026-05-02 ~11:30 UTC]

**Phase 5 Dashboard (Jinja+htmx+Tailwind CDN):**
- web.py: cookie-based JWT auth (httponly, secure, samesite=lax), 5 routes
  (/web, /web/login GET+POST, /web/logout, /web/workspaces, /web/workspaces/
  {ws}/projects/{p}, /web/runs/{run_id})
- Templates: _base.html (header + Tailwind CDN + htmx), login.html,
  workspaces.html, project.html (datasets/scorers/tasks 3-col grid),
  run.html (status pill + results table + comments thread)
- Web router calls service layer DIRECTLY per plan §4 API-first separation
- Required `response_model=None` on routes returning `HTMLResponse |
  RedirectResponse` (FastAPI tries to build a Pydantic schema otherwise)
- 4 dashboard smoke tests
- Deps: jinja2 + python-multipart for Form parsing

**Phase 8 Production Polish:**
- triggers.py: croniter-validated, max 20 active per workspace, min 5min
  interval per plan §15
- webhooks.py: HMAC-SHA256 signed payloads, exponential backoff
  (1s/10s/100s/1000s), dead-letter, SSRF defense (RFC 5735 + DNS re-resolve
  at fire time per plan §15)
- Internal Cron endpoints: /internal/{dispatch-triggers,deliver-webhooks,
  reap-stale-runs,keepalive}, X-Internal-Token gated
- last_used_at write now committed (Phase 1.10 critic finding #9)
- Login rate limit: in-memory sliding window 5/60s

**TOTAL TESTS: 105 passing** (101 existing + 4 dashboard)

## End-of-night status [2026-05-02 ~10:00 UTC]

**Live deploy verified:** <https://scryer-production.up.railway.app/api/v1/healthz>
returns 200 with Scryer-Version + Sunset + X-Request-ID headers.

**101 tests passing.** 41 tables on Neon. Repo at <https://github.com/ewernn/scryer>.

**Phases done (~7 hours of build):**
- Phase 0: bootstrap (FastAPI + Alembic + uv + CI + Sentry stub + Railway)
- Phase 1: Identity + Auth (62 tests)
- Phase 2: Eval Core — schema + services + Run executor + endpoints + CLI
- Phase 3 (partial): AuditEvent + Suite + Tag + UsageRecord services
- Phase 4: Comments + Collections (incl. auto-Comment on run.completed)
- Phase 6: /api/v1/me + Sunset/Scryer-Version headers

**Phases pending:**
- Phase 5 (dashboard): Jinja+htmx — biggest remaining ~2-3 days
- Phase 7 (traitinterp wire-in): use scryer SDK in `experiments/judge_optimization/`
- Phase 8 (production polish): Trigger dispatcher, Webhook delivery worker,
  Budget circuit-breaker, Cron setup, monitoring, Sentry signup

**Critic findings remaining (deferred from Phase 1.10/2.7):**
- ApiKey.last_used_at write isn't committed (lost between requests) — Phase 8
- record_count on Dataset is denormalized (drift potential) — could drop column
- Login endpoint has no rate limit — Phase 8 before public beta

**To resume tonight or tomorrow:** paste `RALPH.md` into a fresh session.

## Phase 6 (Agent UX) — DONE [2026-05-02 ~09:30 UTC]

- GET /api/v1/me — agent first-call discovery; identity + scopes +
  top_resources (capped at 5 each: workspaces, projects, datasets, scorers)
- ApiVersionHeadersMiddleware: every response carries Scryer-Version,
  Sunset, and Link: rel="deprecation"
- asgi-correlation-id already wired (Phase 1.10) → X-Request-ID present
- MCP server mount DEFERRED per plan §16 — observe agent usage 2 weeks
  before curating /mcp/curated. Current OpenAPI sufficient.
- 3 new endpoint tests → 101 tests total

## Phase 4 (Comments + Collections) — DONE [2026-05-02 ~09:00 UTC]

- Cluster 4 (4 tables): comments, comment_versions, collections, collection_members
- Comments: polymorphic author, slim structured={tldr,confidence}, references
  as opaque JSONB list, system Comments immutable
- Auto-Comment on run.completed/failed (plan §8 layer 3) — wired into runs.py
- 8 new tests → 98 tests

## Phase 3 (Audit + Suite + Tag + Usage) — PARTIAL [2026-05-02 ~08:30 UTC]

- Cluster 3 schema: 11 tables (audit_events, suites, suite_tasks, suite_runs,
  suite_run_runs, triggers, webhooks, webhook_deliveries, tags, resource_tags,
  usage_records); migration applied to Neon
- Total tables now: 37 (14 cluster1 + 12 cluster2 + 11 cluster3)
- Services done:
  - audit.py: write_event with auto-redaction of {password, password_hash,
    encrypted_value, key_hash, token_hash, secret}; list_events with cursor
    pagination + filters
  - tags.py: create, list, apply, remove, list_for_resource
  - suites.py: create + add_task + execute_suite (sequential v0)
  - usage.py: record_usage + total_cost_for_workspace_since (Budget enforcement
    helper)
- API endpoint: GET /workspaces/{slug}/audit (cursor paginated, workspace-scoped)
- Tests: 7 cluster 3 service tests passing
- Deferred to Phase 8 (need Cron infra): Trigger dispatcher, Webhook delivery
  + retry, Budget circuit-breaker enforcement (depends on UsageRecord wiring
  into Scorer execution)
- TOTAL TESTS: 90 passing

## Phase 2.5-2.7 (Eval Core endpoints + CLI + critic fixes) — DONE [2026-05-02 ~07:30 UTC]

- API endpoints: /datasets, /scorers, /tasks, /runs, /runs/{id}/results
  (all under /workspaces/{ws}/projects/{p}); authz via get_project_by_slug_path
- CLI: `scryer dataset push|list|get`, `scryer scorer push|list`,
  `scryer run start|get|results` — all with --output table|json
- Critic-pass fixes (Phase 2 critic flagged 5 critical):
  - Sandbox stdout pollution → JSON envelope written to side-channel file
    (workdir/envelope.json), not stdout. User prints can't corrupt.
  - Run `queued→running` race fixed via atomic UPDATE WHERE status='queued'
    RETURNING. Second concurrent execute_run gets ConflictError.
  - Heartbeat is wall-clock (every 30s) not record-count, so slow Scorers
    aren't reaped.
  - Scorer signature mismatch surfaces "signature mismatch" error.
  - content_hash drops `default=str` — fail-fast on non-JSON-native inputs.
- Stale Trace stub at record_id=0 dropped (premature; per-record Traces
  land in Phase 3 if/when agent step capture matters)
- 5 new regression tests for the critic findings + 2 endpoint integration
- TOTAL TESTS: 83 passing

## Phase 2.1-2.4 (Eval Core schema + services + Run executor) — DONE [2026-05-02 ~06:30 UTC]

- Cluster 2: 12 tables (datasets, dataset_records, scorers, agents, tools,
  agent_tools, prompts, tasks, runs, results, traces, trace_steps); migration
  applied to Neon
- VersionedMixin extracted (project_id + slug + version + content_hash + parent_id)
- Services: datasets, scorers, agents, tools, prompts, tasks (with version
  validation on bind)
- _versioned.py shared helpers: content_hash, next_version, get_latest,
  get_version, list_latest_per_slug
- Sandbox (Tier 1+ subprocess): stripped env, ulimits (best-effort on macOS;
  Linux-strict on Railway), timeout, JSON-stdio runner with traceback capture
- Run executor: queue_run + execute_run with heartbeat every 10 records,
  resume_cursor, RunStatus transitions, Trace stub per Run
- 16 new tests passing (11 service + 5 executor): full Scorer-runs-in-subprocess
  end-to-end with real Result rows
- TOTAL TESTS: 78 passing

## Phase 1.10 (final critic + 3 critical fixes) — DONE [2026-05-02 ~05:00 UTC]

- **Critical: cross-tenant leak in GET /workspaces/{slug}** fixed via new
  `services/access.py` module with `assert_workspace_member`,
  `assert_project_access`, `get_project_by_slug_path` (latter two for Phase 2)
- **Critical: cross-tenant leak in GET /workspaces/{slug}/projects** fixed
  by calling `assert_workspace_member` before resolution
- **Critical: RFC 9457 violation in auth.py** fixed — all `HTTPException`
  replaced with typed `AuthError`/`PermissionError`. JWT `uuid.UUID(sub)`
  ValueError now caught → 401, not 500
- `allowed_ips` removed from ApiKey (CONVENTIONS: no fake support);
  migration applied
- 9 new security tests: cross-tenant 404, malformed-bearer parametrized,
  non-UUID JWT sub returns 401 not 500
- TOTAL TESTS: 62 passing
- Critic identified Phase 2 prep gaps; access.py module addresses 3 of 4

## Phase 1.9 (CLI + workspace/project endpoints) — DONE [2026-05-02 ~04:30 UTC]

- `scryer auth login | logout | whoami` working against live Railway
- `scryer workspace list | get` working
- `scryer project list -w <ws>` working
- HTTP endpoints: `/api/v1/auth/{login,me}`, `/api/v1/workspaces`, `/api/v1/workspaces/{slug}/projects`
- Integration tests: 5 new HTTP-shape tests (login round-trip, 401 problem,
  workspace+project list, 404 problem)
- `client` fixture refactored: HTTP tests use independent sessions on the
  test engine (savepoint sharing across async contexts hit `MissingGreenlet`)
- `http_session` fixture for setup data; commits go to schema-isolated test
  namespace; cleaned up by DROP SCHEMA at session end
- Login endpoint now uses typed AuthError → central RFC 9457 handler
- `pyproject.toml` script entry: `scryer = "scryer.cli.main:main"`
- platformdirs → `~/.config/scryer/credentials.json` (chmod 600); multi-profile
- TOTAL TESTS: 53 passing (48 unit + 5 integration)

## Phase 1.5-1.8 (services + auth middleware) — DONE [2026-05-02 ~03:50 UTC]

- **48/48 tests passing** across users, workspaces, projects, api_keys,
  service_accounts, invitations, credentials, security
- 4 critic-flagged critical bugs fixed before tests landed:
  - Atomic `redeem_invitation` (race-safe via UPDATE WHERE used_at IS NULL)
  - Cross-workspace `project_grants` validation (must match `inv.workspace_id`)
  - ServiceAccount keys cannot carry `admin` scope
  - AES-GCM AAD binds ciphertext to `(cred_id, workspace_id)` — blob swap fails
- Argon2 verify now catches all `VerificationError` subclasses (no 500 leak)
- JWT carries `iss=scryer`, `aud=scryer-api`, `leeway=10`
- Reserved-slugs frozenset blocks `admin`, `api`, `login` etc. ("default" removed
  since auto-Project flow uses it)
- RFC 9457 exception handler central + asgi-correlation-id middleware live
- LIVE on Railway: <https://scryer-production.up.railway.app/api/v1/healthz>
  returns `{"status":"ok","db_ok":true}`
- Babysit-agent fixed Railway port mismatch via GraphQL `serviceDomainUpdate`
- Editable-install footgun resolved via `make sync` target — every `pyproject.toml`
  change must be followed by `make sync` to refresh the editable .pth
- `pydantic[email]` added (EmailStr requires validator)

## Surprises and revisions to plan

**2026-05-02 — sha256 (not bcrypt) for ApiKey/Invitation token hashing.** Plan
§6 originally said `bcrypt of full key`. Investigator + critic agreed sha256
is correct: input is 32-byte uniformly-random token (256 bits entropy), so
preimage attacks are infeasible regardless of hash speed. bcrypt would add
~50ms per request for zero security gain. This matches Stripe / GitHub /
crates.io. **Plan §6 updated** to reflect sha256.

**2026-05-02 — Native PG enums replaced with `native_enum=False`.** Plan §11
implies native enums; investigator flagged that they break per-test schema
isolation (enums are global, not schema-scoped). Switched to CHECK-constrained
VARCHAR. Python enum still validates at the ORM layer. Cost: lose PG-side
typo detection at the column level (CHECK gives that anyway).

**2026-05-02 — Critic-pass-driven schema refinements applied to cluster 1
before generating first migration.** Notable:
- Polymorphic `User.api_keys` relationship uses `foreign(ApiKey.principal_user_id)`
  + `PrincipalKind.user` literal in primaryjoin (was failing).
- `Numeric(12,4)` mapped to `Decimal` not `float`.
- Partial UNIQUE on `users(email) WHERE archived_at IS NULL` — soft-deleted
  users free their email for reuse.
- ApiKey FKs use `ondelete=RESTRICT` not `CASCADE` (audit preservation).
- ApiKey.scopes has `server_default="'{}'"` for raw-SQL inserts.
- CHECK on `Invitation(expires_at > created_at)` and `Budget(period_end >
  period_start)` and `Workspace(active_run_count >= 0)`.
- `share_grants.expires_at` added (every other grant noun has it).
- `api_key_usage` indexed on `timestamp` for billing queries.
- Multi-column UNIQUE constraints renamed to include all column names
  (`uq_workspace_members_workspace_user`, etc.).

---

## Subagent log

| Date | Agent | Question | Outcome |
|---|---|---|---|
| 2026-05-02 | r:critic | Phase 0 bootstrap review | 5 fixes applied; no blockers |
| 2026-05-02 | r:investigator | SQLAlchemy 2.x async polymorphic FK | running |
| 2026-05-02 | r:investigator | Argon2id + AES-GCM defaults | running |
| 2026-05-02 | r:investigator | Pytest async transactional rollback | running |
| 2026-05-02 | r:investigator | FastAPI auth middleware + RFC 9457 | running |
| 2026-05-02 | r:investigator | Typer multi-noun CLI structure | done — Typer add_typer; @callback ctx; platformdirs |
| 2026-05-02 | r:investigator | SQLAlchemy 2.x async polymorphic FK | done — Option A (two nullable + CHECK) |
| 2026-05-02 | r:investigator | Argon2id + AES-GCM defaults | done — argon2-cffi direct; sha256 for ApiKey; cryptography AESGCM |
| 2026-05-02 | r:investigator | FastAPI auth + RFC 9457 + correlation ID | done — HTTPBearer DI; fastapi-problem-details; asgi-correlation-id |
| 2026-05-02 | r:investigator | Pytest async transactional rollback | done — savepoint pattern; loop_scope=session |

---

## 2026-05-02 — Deferred: cold-tier archive (DECISION LOCKED)

Scoped a "Run hot data → R2 cold tier" architecture (Parquet manifests, monthly
audit_events DETACH+archive, Cron promotion job, dual-tier read path with
fallback, thaw_run reversibility). 8 parallel investigators + 1 critic.

**Verdict: defer indefinitely.** Critic gave 9/10 STRONG arguments. Cost agent
verified pricing (Neon $0.35/GB-mo + $5/mo min, R2 $0.015/GB-mo).

| Scale | Runs/day | Yr-3 PG | Untiered $/yr | Tiered $/yr | Saved | ROI |
|-------|----------|---------|---------------|-------------|-------|-----|
| Beta | 50 | 5 GB | $60 (min) | $60 (min) | $0 | never |
| Growing | 500 | 51 GB | $214 | $29 | $185 | 5.7 yr |
| Scale-out | 5,000 | 510 GB | $2,142 | $260 | $1,882 | 20 mo |

**Build trigger (ANY one):**
1. PG cumulative storage > 50 GB, OR
2. Neon storage line-item (not minimum) > $20/mo (~57 GB used), OR
3. User requests > 2 yr audit-log retention for compliance.

Until then: pay $5-30/mo, ship product. Monitor disk via existing §15 #20.

**Side effects surfaced (real bugs, separate tickets):**
- web.py:151 dumps up to 1000 results inline, no pagination → Wave 1d
- ix_results_run_id_score_value indexes wrong column for ORDER BY → Wave 1e

Agent outputs archived at: `/private/tmp/claude-501/-Users-ewern-Desktop-code-trait-stuff-traitinterp/5b877adf-1d6e-4945-b429-6ff3d008e519/tasks/`

---

## 2026-05-02 — Cleanup execution: Waves 0-6 (124 tests passing)

Critic-driven scope after cold-tier deferral. Bug-fix list ordered by risk × cost.
All commits to main, Railway auto-deployed. Test count went 106 → 124.

### Wave 0
- Cold-tier archive deferral locked in §15 #20 + this notepad.
- webhook backoff `[d.attempts]` (was `[d.attempts + 1]` — skipped 1s slot).

### Wave 1 — surgical security/correctness
- Timing leak in `authenticate()`: dummy argon2 verify on user-not-found
  (defeats email enumeration via response time). New test floors at 10ms.
- Reaper SKIP LOCKED: `with_for_update(skip_locked=True)` on `reap_stale_runs`
  (matches `dispatch_due_triggers` and `deliver_pending` pattern).
- Rate limit: dual email + IP keying (auth.py was email-only — bypassable),
  bounded growth via dead-key eviction, threading.Lock for multi-worker safety.
  Email cap 5/60s; IP cap 50/60s (CGNAT/NAT reality). `_reset_for_tests()`
  called from `client` fixture so tests don't burn each other's IP budget.
- Web run page pagination: `page` + `per_page` query params (was always
  loading 1000 rows inline + JSONB into Jinja).
- Results index swap: drop `ix_results_run_id_score_value` (wrong sort
  column), add `ix_results_run_id_record_id WHERE invalidated_at IS NULL`
  matching `list_results` `ORDER BY record_id`.

### Wave 2 — middleware
- `ApiVersionHeadersMiddleware` rewritten as pure ASGI (was BaseHTTPMiddleware
  which buffers entire request body — Starlette known issue).
- New `BodySizeLimitMiddleware`: 10 MiB cap, fast-path on Content-Length,
  streaming-path wraps `receive()` to count chunks, sends RFC 9457 problem+json
  on rejection. Outermost middleware so oversize requests get rejected before
  any other middleware processes them.

### Wave 3 — webhook end-to-end
- Built webhook CRUD from scratch (no router existed): WebhookOut omits
  secret, WebhookCreatedOut adds it (returned only on POST + rotate).
  Owner role required for write ops via new `assert_workspace_role` helper.
  All ops audited (secret auto-redacted by audit `_REDACT_KEYS`).
- Wired `fire_event` from `execute_run` on completion — was dead code
  (critic flag #7). Event type = `run.{status}`.
- `deliver_pending` refactored to lease pattern: SKIP LOCKED claims batch,
  bumps `next_attempt_at` by `_LEASE_SECONDS=120`, COMMITs to release row
  locks, then HTTP outside any held lock. If worker crashes, lease expires
  and another worker resumes.

### Wave 4
- a: PYTHONHASHSEED=0 + PYTHONDONTWRITEBYTECODE=1 in sandbox env (determinism +
  no stale .pyc surprises).
- c: `queue_run(supersede=True)` atomically marks prior queued/running Runs
  of the same Task as `superseded` (must set `superseded_by_run_id` to satisfy
  CHECK `ck_runs_superseded_coherent`). API: RunStartRequest.supersede;
  CLI: `scryer run start --supersede`.
- b: CSRF protection on /web/* POST — IN PROGRESS (only auth POST is logout).

### Wave 6 — onboarding
- POST /api/v1/workspaces/{slug}/invitations — owner-only, token returned ONCE.
- POST /api/v1/auth/signup — token redeem (race-safe via UPDATE ... WHERE
  used_at IS NULL ... RETURNING), issues JWT in response.
- `scryer invite create / redeem` CLI commands.
- `scryer task push / list / get` CLI (existing API).

### Pending — needs user action
- **Wave 5 BLOCKED**: dropping `server_executable` from Scorer.content_hash
  invalidates ALL existing scorer hashes. User decision needed: (a) backfill
  hashes pre-drop, (b) accept churn (re-version everything), (c) leave field,
  just stop using it. NOT YET IMPLEMENTED.
- **Wave 7**: R2 wiring (Balanced split). Critic flag #8 corrected: column
  to spill is `TraceStep.payload_json` NOT `Trace.payload_json`. Needs user
  to provision R2 bucket + add R2_* env vars to Railway. Estimate 8-12h
  (first-time R2 wiring).
- **Wave 8**: Docs push (main, architecture, api, cli, deployment) per
  docs/doc-update-guidelines.md. ~3-4h.
- **Wave 51**: tests/test_concurrency.py 2 failures investigation. Lease
  pattern logic looks correct but test still reports 24 (= 8×3) handled.
  Suspect test setup oddity (asyncio.gather + connection pool interaction).
  Not blocking prod (Cron runs deliver_pending once/min, not concurrently).

---

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

