.PHONY: install sync test lint format check ci serve migrate revision

UV ?= ~/.local/bin/uv

install:
	$(UV) pip install -e . --reinstall

sync:
	$(UV) sync --extra dev
	$(UV) pip install -e . --reinstall-package scryer

test:
	$(UV) run pytest -v

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
