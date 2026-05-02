# scryer

Project-agnostic LLM evaluation harness with control-plane-shaped resource
model. Versioned datasets/scorers/agents, sandboxed Run execution, append-only
audit log, multi-user with workspaces + projects.

## Repository Structure

```
scryer/
├── src/scryer/
│   ├── cli/                 # Typer CLI: one file per noun
│   ├── config.py            # pydantic-settings (single env source)
│   └── server/
│       ├── app.py           # FastAPI factory + middleware + routers
│       ├── auth.py          # HTTPBearer Depends → Principal dataclass
│       ├── db.py            # async engine + get_session
│       ├── middleware.py    # Scryer-Version + Sunset headers
│       ├── exception_handlers.py  # ScryerError → RFC 9457 Problem
│       ├── api/             # JSON endpoints, one file per noun
│       ├── services/        # Pure async business logic, one file per noun
│       └── models/          # SQLAlchemy 2.x models by cluster
│           ├── auth.py      # cluster 1: identity + org + auth
│           ├── eval.py      # cluster 2: datasets, scorers, runs, results
│           ├── audit.py     # cluster 3: audit_events, suites, triggers, usage
│           └── collab.py    # cluster 4: comments, collections
├── migrations/              # Alembic
└── tests/                   # pytest async; schema-isolated test namespace
```

## Quick Start

```bash
make sync           # uv sync + reinstall editable
make migrate        # alembic upgrade head
make serve          # uvicorn on http://127.0.0.1:8000
make test           # pytest
```

Hit `http://127.0.0.1:8000/api/openapi.json` for the full surface.
Live: <https://scryer-production.up.railway.app/api/v1/healthz>

## Key Entry Points

**Authenticate:**
```bash
scryer auth login --email you@example.com
scryer auth whoami
```

**Push a Dataset + Scorer + Task → run it:**
```bash
scryer dataset push -w {ws} -p {project} -s golden -n "Golden" -f records.json
scryer scorer push -w {ws} -p {project} -s coh -n "Coherence" -f scorer.py
# Bind a Task via API (CLI command coming)
scryer run start {task_id}
scryer run results {run_id}
```

**Inspect audit log:**
```bash
GET /api/v1/workspaces/{slug}/audit
```

## Core Components

### Auth (cluster 1)
**Purpose:** multi-user identity, ApiKeys, encrypted Credentials
**Key files:**
- `services/security.py` — argon2id, sha256-for-tokens, AES-GCM, JWT
- `services/access.py` — `assert_workspace_member`, `assert_project_access`,
  `get_project_by_slug_path`

### Eval Core (cluster 2)
**Purpose:** versioned eval primitives + Run executor
**Key files:**
- `services/_versioned.py` — content_hash + next_version + lineage
- `services/runs.py` — atomic queued→running, heartbeat, Result persistence
- `services/sandbox.py` — Tier 1+ subprocess (stripped env, ulimits, timeout,
  side-channel envelope)

### Audit + Automation (cluster 3, partial)
**Purpose:** append-only AuditEvent log, Suite execution, UsageRecord
**Key files:**
- `services/audit.py` — write_event with auto-redaction
- `services/suites.py` — execute_suite (sequential v0)
- `services/usage.py` — record_usage + cost aggregation

### Collaboration (cluster 4)
**Purpose:** Comments (with version history) + Collections (curated bundles)
**Key files:**
- `services/comments.py` — write_comment, edit_comment (with auto-versioning),
  write_system_comment for plan §8 layer-3 events
- `services/collections.py` — create + add_member with group/note/position

## Architecture

```
api/         <- HTTP boundary (RFC 9457 errors)
    |
    v
services/    <- pure async business logic
    |
    v
models/      <- SQLAlchemy 2.x
    |
    v
Postgres (Neon) + R2 (planned for Trace blobs)
```

**Auth flow:** `Authorization: Bearer <token>` → `get_principal` Depends
dispatches by prefix (`scrk_live_*` → ApiKey path; dot-separated → JWT path)
→ `Principal{id, kind, scopes, api_key_id}` injected into endpoints.
`require_scope("write")` factory wraps for scope enforcement.

**Errors:** every endpoint error returns `application/problem+json` per
RFC 9457 with `retryable` extension. Service layer raises typed errors
(`NotFoundError`, `ConflictError`, `AuthError`, `PermissionError`,
`ValidationError`, `IntegrityError`); central handler maps to Problem.

**Versioning:** Dataset/Scorer/Agent/Tool/Prompt/Task share `VersionedMixin`:
`(project_id, slug, version)` UNIQUE + `parent_id` lineage chain +
content_hash. Pushing a new version mints a new immutable row.

## Navigation Guide

To work on:
- New API endpoint -> `src/scryer/server/api/{noun}.py`
- New business logic -> `src/scryer/server/services/{noun}.py`
- New schema -> add to `models/{cluster}.py`, then `make revision`
- CLI command -> `src/scryer/cli/{noun}.py` + register in `cli/main.py`
- Tests -> `tests/test_{noun}.py` (uses `session` or `client` fixture)

## Current Status

**Working:**
- Phase 0: repo bootstrap, FastAPI, Alembic, Sentry stub, CI green
- Phase 1: identity + auth + workspaces/projects (62 tests)
- Phase 2: eval core (datasets, scorers, agents, tools, prompts, tasks,
  runs, results, traces); end-to-end Run execution in subprocess sandbox
- Phase 3 (partial): AuditEvent, Suite, Tag, UsageRecord services
- Phase 4: Comments + Collections + auto-Comment on Run completion
- Phase 6: /api/v1/me, Scryer-Version + Sunset headers

**Deployed:** <https://scryer-production.up.railway.app/api/v1/healthz>

**Not Working / Deferred:**
- Phase 5 dashboard (Jinja+htmx) — not built
- Trigger dispatcher + Webhook delivery worker — need Cron infra (Phase 8)
- Budget circuit-breaker enforcement — depends on UsageRecord wiring into
  Scorer execution
- MCP server mount — observe agent usage 2 weeks before curating
- Custom-deps-per-Scorer — pre-baked image only in v0
- Phase 7 wire-in to traitinterp's `experiments/judge_optimization/` — pending

## Additional Documentation

The dev-side build notepad is `scryer_notepad.md` (root). Conventions in
`CONVENTIONS.md`. Wake-up prompt for resuming builds in `RALPH.md`. Strategic
plan-of-record lives in the originating repo at
`/Users/ewern/Desktop/code/trait-stuff/traitinterp/docs/other/may2_scryer_plan.md`.

---

## Documentation Update Guidelines

- **Delete first, add second** — remove outdated content before adding new
- **Present tense only** — document what IS, not what WAS
- **No history** — no changelogs, no "previously", no migration notes
- **YAGNI for docs** — delete unused sections immediately
