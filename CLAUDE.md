@docs/main.md

## Code Principles (binding)

User HATES: duplicate code, verbose code, long files, ceremony comments.
User LOVES: minimal clean files, single source of truth, fail-fast errors.

**Apply religiously:**

- Default: NO comments. Add only when WHY is non-obvious.
- Module docstring ≤ 2 lines. Function docstring only if behavior is
  non-obvious from name + signature.
- Service functions: take `session: AsyncSession` as first arg, raise typed
  errors (`scryer.server.services.errors`) NOT `HTTPException`.
- Endpoints: every route has `response_model=` + `operation_id=`. Use
  `Annotated[X, Depends(...)]`.
- Tests: per-noun unit + cross-noun integration; use the `session` or `client`
  fixture from `conftest.py`. No DB mocks — savepoint rollback against
  schema-isolated test namespace.
- Async everywhere. CPU-bound work via `asyncio.to_thread`.
- Authz: NEVER skip the `assert_workspace_member` / `assert_project_access`
  / `get_project_by_slug_path` helpers in API endpoints. Cross-tenant data
  leaks have shipped twice; both caught by critic; helpers exist exactly
  to prevent regressions.

## Workflow

After every substantive change:
```
make ci          # ruff + format + mypy + pytest
git add -A && git commit -m "..." && git push
```

Update `scryer_notepad.md` with substage status + any plan deviations.
Reflect plan revisions back into the source-of-truth at
`traitinterp/docs/other/may2_scryer_plan.md`.

## Spawning subagents

Spawn liberally for non-trivial work (investigators, critics). Especially
critic-pass at every phase boundary. Unlimited Claude Code usage.
