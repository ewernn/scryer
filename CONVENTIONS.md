# scryer code conventions

User preferences (locked):
- HATES: duplicate code, verbose code, long files, ceremony comments
- LOVES: minimal clean files, single source of truth

Apply these or rewrite. Brevity is correctness.

## Comments + docstrings

- **Default: no comments.** Only when WHY is non-obvious.
- **Module docstring**: ≤ 2 lines. State purpose, not contents.
- **Function docstring**: only if behavior is non-obvious from name + signature.
  Never restate the args.
- **No section banners** like `# ─── X ──────`. Use blank lines.
- Never write a comment that says what the code already says.

## File length

- Keep service files ≤ 200 lines. Split by sub-noun if larger.
- Keep models split by cluster (already done).
- Keep API endpoint files ≤ 150 lines per noun.
- Repeated cross-cutting logic → factor into a helper, not duplicate.

## Imports

- `from __future__ import annotations` at the top of every Python file.
- Type-only imports under `if TYPE_CHECKING:`.

## Service layer

- One file per noun in `src/scryer/server/services/`.
- All functions take `session: AsyncSession` as first arg (keyword-or-positional fine).
- Never raise `HTTPException` from a service. Use typed errors from `services/errors.py`.
- Return ORM rows or domain dataclasses. Never return Pydantic API models from
  the service layer (that's API-layer concern).
- Functions are async. Use `await session.flush()` not `commit()` inside services
  — the API/CLI layer commits.

## API layer

- One file per noun in `src/scryer/server/api/`.
- Every endpoint declares `response_model=` and `operation_id=`.
- Use `Annotated[X, Depends(...)]` syntax (not the older `X = Depends(...)` form).
- Service errors are mapped to RFC 9457 problems by a single exception handler
  in `app.py` — endpoint code does NOT translate errors itself.

## Tests

- Per-noun unit tests in `tests/test_<noun>.py`.
- Integration tests for cross-noun flows in `tests/integration/test_<flow>.py`.
- Use the `session` fixture (savepoint rollback). Use `client` fixture for HTTP.
- Don't mock the DB; tests run against a real schema-isolated Neon namespace.
- Cover: happy path, conflict, not-found, permission denied, expired/revoked.

## Names

- Functions describe behavior at the right abstraction (`create_workspace`, not
  `create` or `create_workspace_with_owner_and_default_project`).
- Variables: descriptive over short. `principal_kind` not `pk`.
- File names match the dominant noun (`api_keys.py`, not `auth_helpers.py`).

## Async

- All DB calls async. Never use sync session.
- CPU-bound work (argon2): wrap in `asyncio.to_thread`.

## Errors

- Typed errors per category (`NotFoundError`, `ConflictError`, `AuthError`).
- Never raise stringly-typed `Exception("not found")` — use `NotFoundError`.

## Decision log

- Every non-obvious decision goes in `scryer_notepad.md` "Surprises" section.
- Every locked plan deviation gets reflected in the plan doc too.
