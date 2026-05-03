.PHONY: install sync test test-migrations lint format check ci serve migrate revision

UV ?= ~/.local/bin/uv

install:
	$(UV) pip install -e . --reinstall

sync:
	$(UV) sync --extra dev
	$(UV) pip install -e . --reinstall-package scryer

test:
	$(UV) run pytest -v

# End-to-end Alembic migration test: docker postgres + upgrade + smoke + down + re-up.
# Use this before landing any migration that adds triggers, RLS policies,
# or anything else that Base.metadata.create_all (used by `make test`) skips.
test-migrations:
	UV=$(UV) bash scripts/test_migration.sh

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

check: lint
	$(UV) run mypy src

ci: check test

serve:
	$(UV) run uvicorn scryer.server.app:app --reload --host 127.0.0.1 --port 8000

migrate:
	$(UV) run alembic upgrade head

revision:
	@read -p "Migration message: " msg; \
	$(UV) run alembic revision --autogenerate -m "$$msg"
