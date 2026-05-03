# scryer — documentation

LLM evaluation harness with a control-plane-shaped resource model. Multi-user,
server-side Run execution, content-hashed versioning, audit log,
cron-triggered Suites, outbound webhooks, MCP server.

**Live:** <https://scryer-production.up.railway.app/api/v1/healthz>
**Source:** <https://github.com/ewernn/scryer>

## Documentation index

- **[architecture.md](architecture.md)** — schema clusters, polymorphic FK
  pattern, sandbox model, Run lifecycle, audit semantics
- **[deployment.md](deployment.md)** — Railway + Neon + R2 provisioning, env
  vars, `make` targets, migration runbook
- **[cli_reference.md](cli_reference.md)** — every `scryer` subcommand with
  examples (auth, invite, workspace, project, dataset, scorer, task, run)
- **API reference** — auto-generated OpenAPI at
  [`/api/docs`](https://scryer-production.up.railway.app/api/docs) (Swagger UI)
  and [`/api/openapi.json`](https://scryer-production.up.railway.app/api/openapi.json) (machine-readable)

## Quick start (install)

```bash
git clone https://github.com/ewernn/scryer
cd scryer
make sync           # uv sync + reinstall editable
cp .env.example .env  # then fill in DATABASE_URL, R2_*, JWT_SECRET, ENCRYPTION_KEY
make migrate        # alembic upgrade head
make serve          # uvicorn on http://127.0.0.1:8000
```

## Quick start (use)

Bootstrap the first owner via direct DB insert (no signup endpoint can issue
the FIRST workspace), then everyone else joins via invitation:

```bash
# After signup (token-gated):
scryer auth login --email you@example.com
scryer invite create -w <workspace> -e teammate@example.com -r member
# Send the printed token to teammate; they:
scryer invite redeem -t <token> -e teammate@example.com
```

Push primitives, run an eval:

```bash
scryer dataset push -w <ws> -p <proj> -s golden -n "Golden" -f records.json
scryer scorer push  -w <ws> -p <proj> -s coh    -n "Coherence" -f scorer.py
scryer task push    -w <ws> -p <proj> -f task.json
scryer run start <task_id>           # synchronous v0; returns Result counts
scryer run results <run_id>          # per-record table
```

Inspect audit log:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/workspaces/<ws>/audit?limit=50"
```

Subscribe a webhook for `run.done` / `run.failed`:

```bash
curl -H "Authorization: Bearer $TOKEN" -d '{
  "name": "ci-hook", "url": "https://your.app/scryer-hook",
  "event_types": ["run.done", "run.failed"]
}' "$BASE_URL/api/v1/workspaces/<ws>/webhooks"
# Response includes the HMAC `secret` ONCE — store it.
```

## Stack

- Python 3.12+ · FastAPI · SQLAlchemy 2.x async · Pydantic 2 · Typer
- PostgreSQL 17 (Neon) · Cloudflare R2 for Trace blobs
- argon2id passwords · sha256 ApiKey/Invitation tokens · AES-GCM Credentials · pyjwt HS256 sessions
- RFC 9457 problem+json errors · X-Request-ID via asgi-correlation-id
- Server-rendered Jinja + htmx + Tailwind dashboard at `/web/*`
- Hosted on Railway · CI via standard `pytest` (no GitHub Actions yet)

## Repository layout

```
scryer/
├── src/scryer/
│   ├── cli/                 # Typer subapps, one file per noun
│   ├── config.py            # pydantic-settings (single env source)
│   └── server/
│       ├── app.py           # FastAPI factory, middleware, router mounts
│       ├── auth.py          # HTTPBearer → Principal dispatcher
│       ├── db.py            # async engine + per-request session
│       ├── middleware.py    # ApiVersionHeaders + BodySizeLimit (pure ASGI)
│       ├── exception_handlers.py  # ScryerError → RFC 9457 Problem
│       ├── api/             # JSON endpoints
│       ├── services/        # pure async business logic
│       └── models/          # SQLAlchemy 2.x by cluster
├── migrations/              # Alembic
└── tests/                   # pytest async; schema-isolated test namespace
```

## Doc maintenance

- **Docs reflect reality.** Update in the same commit as the code change.
- **Present tense only.** No "previously", no migration notes, no changelog.
- **Single source of truth.** Cross-link rather than duplicate.
- **YAGNI for docs.** Delete unused sections immediately.

The dev notepad (`/scryer_notepad.md`) holds the live build log and locked
decisions; it is NOT part of public docs.
