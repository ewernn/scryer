.PHONY: install sync unhide-pth test test-migrations lint format check ci serve migrate revision

UV ?= ~/.local/bin/uv

install:
	$(UV) pip install -e . --reinstall
	$(MAKE) unhide-pth

sync:
	$(UV) sync --extra dev
	$(UV) pip install -e . --reinstall-package scryer
	$(MAKE) unhide-pth

# iCloud Desktop sync flips the macOS UF_HIDDEN bit on freshly-written .pth
# files. Python 3.14's site.addpackage skips hidden .pth files, so the
# editable install becomes invisible after every `uv pip install -e .`.
# Clear the flag (no-op on Linux/CI).
unhide-pth:
	@if [ -d .venv/lib/python3.14/site-packages ] && command -v chflags >/dev/null 2>&1; then \
		chflags nohidden .venv/lib/python3.14/site-packages/*.pth 2>/dev/null || true; \
	fi

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
