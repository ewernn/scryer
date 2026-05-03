#!/usr/bin/env bash
# End-to-end migration test: spins up a fresh Postgres in docker, runs
# every Alembic migration up + down + up, then a Python smoke test that
# inserts + selects against the migrated schema via the ORM.
#
# Catches:
# - migration syntax errors (alembic upgrade head fails)
# - non-reversible migrations (alembic downgrade base fails)
# - migrations that aren't idempotent on re-up
# - ORM/schema drift (smoke test inserts via SQLAlchemy → checks column exists)
# - PG-specific code (triggers, RLS, CHECKs) that Base.metadata.create_all skips

set -euo pipefail

CONTAINER=scryer_migration_test
PORT=${SCRYER_TEST_MIGRATION_PORT:-54331}
PG_IMAGE=${SCRYER_TEST_MIGRATION_PG_IMAGE:-postgres:17-alpine}

cleanup() {
    docker stop "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Removing any stale container"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo "==> Starting Postgres (image: $PG_IMAGE) on localhost:$PORT"
docker run -d --rm \
    --name "$CONTAINER" \
    -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=scryer_test \
    -p "$PORT:5432" \
    "$PG_IMAGE" >/dev/null

echo "==> Waiting for Postgres to accept connections"
for i in {1..30}; do
    if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then
        echo "    ready after ${i}s"
        break
    fi
    sleep 1
    if [[ $i -eq 30 ]]; then
        echo "ERROR: Postgres never became ready after 30s"
        docker logs "$CONTAINER" || true
        exit 1
    fi
done

export DATABASE_URL="postgresql+asyncpg://postgres:test@localhost:$PORT/scryer_test"
echo "==> DATABASE_URL=$DATABASE_URL"

UV=${UV:-~/.local/bin/uv}

echo "==> ensuring editable install is current"
$UV pip install -e . --reinstall-package scryer >/dev/null

echo "==> alembic upgrade head (initial)"
$UV run alembic upgrade head

echo "==> smoke test against migrated schema"
$UV run python scripts/smoke_migration.py

echo "==> alembic downgrade base (verifies all migrations are reversible)"
$UV run alembic downgrade base

echo "==> alembic upgrade head (verifies re-up after down)"
$UV run alembic upgrade head

echo "==> smoke test again (post re-up)"
$UV run python scripts/smoke_migration.py

echo "==> ALL MIGRATION CHECKS PASSED"
