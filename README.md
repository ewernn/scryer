# scryer

> Project-agnostic LLM evaluation harness with a control-plane-shaped resource model.

Multi-user, server-side Run execution, content-hashed versioning, audit log,
cron-triggered Suites, outbound webhooks, MCP server.

**Live:** <https://scryer-production.up.railway.app/api/v1/healthz>

## Documentation

- **[docs/main.md](docs/main.md)** — entry point + quick start
- **[docs/architecture.md](docs/architecture.md)** — schema, sandbox, Run lifecycle, audit
- **[docs/deployment.md](docs/deployment.md)** — Railway + Neon + R2 provisioning
- **[docs/cli_reference.md](docs/cli_reference.md)** — every `scryer` subcommand
- **API reference** — `/api/docs` (Swagger UI), `/api/openapi.json` (machine-readable)

## Quick start

```bash
git clone https://github.com/ewernn/scryer
cd scryer
make sync && cp .env.example .env  # fill DATABASE_URL, R2_*, JWT_SECRET, ENCRYPTION_KEY
make migrate && make serve
```

Then:

```bash
scryer auth login --email you@example.com
scryer dataset push -w <ws> -p <proj> -s golden -n "Golden" -f records.json
scryer scorer  push -w <ws> -p <proj> -s coh    -n "Coherence" -f scorer.py
scryer task    push -w <ws> -p <proj> -f task.json
scryer run     start <task_id>
scryer run     results <run_id>
```

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.x async · Pydantic 2 · Typer · PostgreSQL 17 (Neon) · Cloudflare R2 · Jinja + htmx + Tailwind dashboard · MCP server · Railway hosting.

## License

Apache-2.0
