# Deployment

## Hosting

`scryer` runs on Railway. Source on `main` auto-deploys. Health check:
`/api/v1/healthz` returns `{"status":"ok","db_ok":true}`.

## Environment variables

| Var | Purpose | Required | Format |
|---|---|---|---|
| `DATABASE_URL` | Postgres async URL | yes | `postgresql+asyncpg://user:pw@host/db?ssl=require` |
| `R2_ENDPOINT` | Cloudflare R2 S3-API URL (no bucket suffix) | yes | `https://<acct>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | R2 token access key | yes | 32-hex |
| `R2_SECRET_ACCESS_KEY` | R2 token secret | yes | 64-hex |
| `R2_BUCKET_NAME` | Bucket for Trace + archive blobs | yes | e.g. `scryer-blobs` |
| `JWT_SECRET` | HS256 signing key for session JWTs | yes | 32+ random chars |
| `JWT_TTL_SECONDS` | Session lifetime | no (default 3600) | int |
| `ENCRYPTION_KEY` | AES-GCM key for Credentials at rest | yes | hex-encoded 32 bytes |
| `ENCRYPTION_KEY_VERSION` | For key rotation | no (default 1) | int |
| `SENTRY_DSN` | Error tracking | no | Sentry DSN URL |
| `SENTRY_TRACES_SAMPLE_RATE` | APM sampling | no (default 0.0) | 0.0–1.0 |
| `INTERNAL_TOKEN` | Bearer for `/internal/*` cron endpoints | yes | random secret |
| `ENV` | Sentry environment tag | no (default `dev`) | `dev` / `staging` / `prod` |
| `RAILWAY_ENVIRONMENT` | Auto-set by Railway; gates secure-cookie flag | n/a | — |

## Provisioning checklist

### Postgres (Neon)

1. Create a Neon project (one branch is fine for v0).
2. Copy the **pooled** connection string (`-pooler` host).
3. Append `?ssl=require`; replace driver with `+asyncpg`.

### R2 (Cloudflare)

1. Create a bucket (e.g. `scryer-blobs`).
2. **Manage R2 API Tokens** → **Create API Token**:
   - Permissions: **Object Read & Write**
   - Specify bucket: select the new bucket (limits blast radius)
3. Copy Access Key ID + Secret (shown once).
4. The S3-API endpoint URL is `https://<account-id>.r2.cloudflarestorage.com` —
   omit the bucket suffix when setting `R2_ENDPOINT`.

### Generate secrets

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'  # JWT_SECRET
python -c 'import secrets; print(secrets.token_hex(32))'      # ENCRYPTION_KEY
python -c 'import secrets; print(secrets.token_urlsafe(32))'  # INTERNAL_TOKEN
```

### Railway setup

```bash
railway login
railway link --project <project-id>
railway variables --service scryer --environment production \
  --set "DATABASE_URL=..." \
  --set "R2_ENDPOINT=..." --set "R2_ACCESS_KEY_ID=..." \
  --set "R2_SECRET_ACCESS_KEY=..." --set "R2_BUCKET_NAME=scryer-blobs" \
  --set "JWT_SECRET=..." --set "ENCRYPTION_KEY=..." \
  --set "INTERNAL_TOKEN=..."
git push origin main   # triggers deploy
```

## Make targets

```
make sync         # uv sync --extra dev + reinstall editable scryer
make migrate      # alembic upgrade head
make serve        # uvicorn --reload on 127.0.0.1:8000
make test         # pytest -v (skips R2 tests if creds unset)
make check        # ruff + mypy
make format       # ruff format + ruff check --fix
make ci           # check + test (the gate before commit)
```

## Migrations

```bash
make migrate      # apply all pending
~/.local/bin/uv run alembic revision --autogenerate -m "msg"   # author new
~/.local/bin/uv run alembic downgrade -1                       # roll back one
```

The Procfile runs `alembic upgrade head` before booting uvicorn, so production
deploys auto-apply migrations. Online migrations should still be reviewed for
locking; large-table ALTERs go behind the planned maintenance-mode flag.

## Cron endpoints

The `/internal/*` namespace is gated by `Authorization: Bearer $INTERNAL_TOKEN`.
Hit each from a Railway Cron service or external scheduler:

| Endpoint | Cadence | Purpose |
|---|---|---|
| `POST /internal/dispatch-triggers` | every 1 min | fire due Triggers |
| `POST /internal/deliver-webhooks` | every 1 min | drain WebhookDelivery queue |
| `POST /internal/reap-stale-runs` | every 1 min | mark heartbeat-timed-out Runs failed |

## Observability

- **Health**: `GET /api/v1/healthz` (used by Railway's health check).
- **Errors**: Sentry, sample rate via `SENTRY_TRACES_SAMPLE_RATE`.
- **Audit**: every workspace's `AuditEvent` log via
  `GET /api/v1/workspaces/{slug}/audit?before_id=<cursor>`.
- **Request tracing**: every response carries `X-Request-ID` (asgi-correlation-id).

## Local development

`.env` in repo root (gitignored) is loaded by pydantic-settings. Same vars
as production, except `DATABASE_URL` typically points at a local Postgres or
a separate Neon test branch and the R2 bucket is shared with prod (path
prefixes prevent collisions).
