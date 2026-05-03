# Operating principles — read at every loop iteration

This file changes rarely. The notepad changes every iteration. Together
they keep the loop disciplined.

## The core philosophy

**Push invariants down the stack: from convention → application →
database → cryptography.** Every wave moves an invariant from
"developers will remember to enforce this" to "the system structurally
cannot violate it." This is the through-line of the entire pre-launch
redesign queue.

Concretely:
- Tenant isolation: convention (`assert_workspace_member`) →
  application (`require_workspace_from_path` Depends on every router) →
  database (RLS policies on every multi-tenant table)
- Identity: convention (slug+name strings) → cryptography
  (`content_hash` SHA-256 with FROZEN CONTRACT)
- Cancellation: status field → killed subprocess
- Idempotency: client retries are your problem → server-enforced key
- Audit emission: convention (every service calls `write_event`) →
  database (SQLAlchemy after_insert/update/delete listener — Wave R1)

When a new feature requires a new invariant, ask: which layer can
enforce it? Pick the deepest one that's reasonable.

## No shortcuts — concrete rules

### 1. Don't write code that "looks right but doesn't work"

If you don't have evidence (tests passing, manual verification, type
checks) that code works, don't commit it. Spawn a subagent to verify if
you're unsure.

### 2. Don't add `# TODO` or `# FIXME` comments

Either fix it now, OR open a task via TaskCreate and reference it in the
notepad. Comments rot in the codebase invisibly; tasks are visible.

### 3. Don't bypass `make ci` or `make test-migrations`

These exist because they catch real bugs (D4 caught the asyncpg
multi-statement bug on its first run). If they fail, fix the underlying
issue. Never `git commit --no-verify`.

### 4. Don't apply a migration without `make test-migrations` validating it

Migrations are the highest-risk class of changes. The CI pipeline
validates upgrade + downgrade + re-up + smoke against an isolated docker
postgres. Five seconds of validation prevents hours of prod debugging.

### 5. Don't add fallback values for "things that can't happen"

`fallback_value if not real_value else real_value` hides bugs. If the
real value is missing, fail fast. Use `raise ValueError(...)`, not a
silent default.

### 6. Don't mock the database in tests

Use real PG via the docker conftest. Real SQL paths catch real bugs.
Mocks rot — they pass when the schema changes underneath them.

### 7. Don't duplicate constants

If `MIN_COHERENCE = 77` lives in `scorers.py`, don't write
`MIN_COHERENCE = 70` as a fallback in another file. Single source of
truth, always. Re-typing a constant means you'll forget to update one
of them in 3 months.

### 8. Don't write defensive code at internal boundaries

Validate at system boundaries (HTTP request, CLI argv, env var). Trust
internal code. If `services/runs.py` calls `services/audit.py`, the
audit function shouldn't re-validate every parameter — that's noise.

## Spawn subagents liberally

The user has unlimited Claude Code usage and explicitly wants parallel
investigation. Default to spawning rather than reasoning alone.

Specific patterns that work:

- **Pre-design**: spawn 3-5 `r:investigator` in parallel with
  different angles. Reflect on findings between waves.
- **Pre-implementation**: spawn `r:critic` to stress-test the plan
  before writing code.
- **Post-implementation of foundational changes**: spawn `r:verifier`
  + another `r:critic` to vet the work. Apply findings before
  shipping.
- **Scope-out** for waves > 0.5d: use `/z-scope-out` skill — it
  automates the "investigate + reflect + critique + ask user
  questions" loop.
- **Double-check** agents you've already used: `/z-double-check-agents`
  before trusting their output for foundational decisions.
- **Independent waves**: spawn agents to do them in parallel. The
  agent doing the model changes (workspace_id on 20 ORM models) ran
  in the background while you wrote test wrappings.

When NOT to spawn: trivial mechanical edits (one-line typos,
straightforward formatting), things you can verify in <30 seconds, or
when the cost of getting it wrong is small + immediately reversible.

## Notepad discipline

`scryer_notepad.md` is the canonical state. Keep it current.

- **Append a brief note** to the current session entry after every
  substage. Wave name, what you did, any surprises.
- **Never delete entries** — the historical record is part of the
  state. Future-you needs to see "we tried X, it failed because Y"
  to avoid re-trying.
- **For new sessions**: add a `## YYYY-MM-DD — <session theme>`
  header at the bottom. Each session's work goes under its own
  header.
- **Keep the "Remaining queue" section current** at the bottom of
  the latest session entry. This is what loop iterations read first.
- **Decisions go in the notepad**, not in chat-only memory. If you
  decide "X over Y because Z", write it. The next loop iteration
  needs to see Z.

## When to stop the loop

Real blockers (write the ask, exit the iteration):
- User credentials / API keys / connection strings needed
- Destructive action requires user approval (drop table, force push)
- External infra provisioning needed (new R2 bucket, new env vars)
- Choice between 2+ reasonable paths with different long-term
  tradeoffs, undocumented in plan/notepad

NOT real blockers (handle yourself):
- Test failures (debug + fix)
- Type errors (fix the type)
- Lint errors (fix or refactor)
- Migration syntax errors
- Schema drift between models and migration
- Agent output quality questionable (spawn another)

When you hit a real blocker:
1. Write the ask clearly to scryer_notepad.md under a `### BLOCKED:
   <what>` heading
2. Commit + push the WIP state
3. Output the blocker text and exit the iteration

The user will paste the answer back when they're awake.

## Code style (referenced from CONVENTIONS.md)

- No comments unless WHY is non-obvious
- Module docstrings ≤ 2 lines
- Service files ≤ 200 lines (split by sub-noun if larger)
- API endpoint files ≤ 150 lines per noun
- `from __future__ import annotations` at top
- Type-only imports under `if TYPE_CHECKING:`
- Naming: descriptive enough to make behavior obvious without reading
  the implementation. `compute_score()` over `proc()`; `assert_workspace_role()`
  over `check()`.
