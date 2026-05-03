# scryer notepad — INDEX

This is the loop's first read. Slim by design — historical detail lives
in `notepad/` per-session files. Update **this file** at every wave
boundary; update the relevant `notepad/*.md` for in-wave detail.

## Current state

- **Live**: <https://scryer-production.up.railway.app/api/v1/healthz>
  (`db_ok: true` after every push)
- **Migration head in prod**: `a1b2c3d4e5f6` (idempotency_keys table).
  Recent migrations on `main`: f3c5d8e0a712 (time-as-DB-truth),
  e9f1a4c8b3d6 (audit_events strict isolation), d8a719c5b2e7 (slug
  history triggers), c4f2e1b9a3d5 (Phase 1c FORCE RLS).
- **Tests**: 185 passing in ~22s
- **Active wave**: Wave 2+5 BUNDLED (cascade-down soft-delete +
  archived_at-as-RLS-predicate) — see "Wave 2+5 NEXT" below

## Wave 1 SHIPPED (2026-05-03 evening)

Idempotency-Key middleware as a Depends. Stripe semantics: silent on
missing header, replay returns cached, 422 on body mismatch, 409 on
in-flight, 4xx cached / 5xx not cached, 24h TTL. Two-tier table
(workspace + non-workspace) with two RLS policies (workspace +
principal isolation). Wired on POST /workspaces/{slug}/runs as first
consumer; replay returns 'Idempotent-Replay: true' header. 3 tests in
test_idempotency.py. To wire on more endpoints later: add
`dependencies=[Depends(check_idempotency)]` + `request: Request,
background_tasks: BackgroundTasks` params + before-return call to
`capture_idempotency_response(...)`.

## Wave 2+5 NEXT — cascade-down + archived_at RLS (BUNDLED)

CRITIC BLOCKER from scope-out: must bundle. Adding `archived_at IS
NULL` to USING without explicit `WITH CHECK (workspace_id only)`
breaks every soft-delete UPDATE.

Implementation per agent specs (in `notepad/wave_2_5_plan.md` if
written; otherwise from scope-out report in conversation history):

1. New migration:
   a. ADD COLUMN webhooks.archived_at if missing
   b. CREATE FUNCTION _trgfn_cascade_archive_workspace() — fans
      out archived_at from workspace to projects + service_accounts
      + credentials + budgets + webhooks via bulk UPDATE WHERE
      workspace_id=NEW.id AND archived_at IS NULL
   c. CREATE TRIGGER on workspaces AFTER UPDATE OF archived_at
      WHEN (NEW.archived_at IS NOT NULL AND OLD.archived_at IS NULL)
   d. DROP+CREATE policy on 8 SoftDelete RLS tables (datasets,
      scorers, agents, tools, prompts, tasks, projects, credentials):
      USING (ws=guc AND (archived_at IS NULL OR
             current_setting('app.include_archived', true) = 'true'))
      WITH CHECK (ws=guc)
2. archive_workspace service in services/workspaces.py — sets
   archived_at + bulk UPDATE Run.status='cancelled' for queued/running
3. DELETE /workspaces/{slug} endpoint — owner-only, idempotent (re-
   archive returns 204 because trigger WHERE filters)
4. apply_workspace_context gains optional include_archived kwarg;
   _set_rls_context emits set_config('app.include_archived', ..., true)
5. Tests: cascade reach + RLS hide + USING/WITH CHECK split verification

## Wave 3 NEXT-NEXT — PID-tracking cancellation

Add Run.executor_pid column + module-level _active_procs dict in
services/runs.py + sandbox.run_user_code on_proc_start callback +
cancel_run does proc.terminate() + 5s grace + proc.kill().
Cooperative status check between records. ~2-3h.

## Most recent iteration's wave (post-Phase-1c hardening)

Critic audit after Phase 1c shipped surfaced 4 critical RLS gaps
that tests didn't catch (engine fixture is SUPERUSER, bypasses RLS).
All four fixed:
- /web/runs/{id} → /web/workspaces/{slug}/runs/{id} (URL nest, GUC set)
- audit_events policy tightened to drop NULL-workspace cross-tenant leak
- MAX_TRIGGERS_PER_WORKSPACE filter switched from project_id to workspace_id
- _assert_sa_workspace now also checks workspace.archived_at

Plus reflector recommendations:
- Principal carries workspace_id for SA — eliminates per-request DB lookup
- TimestampMixin uses server_default now() — kills clock skew across processes

## Read order at every loop iteration

1. `git log --oneline -15` — what landed recently
2. **this file** — current state + active wave
3. `notepad/phase_1c_checklist.md` (or whichever wave is active)
4. `PRINCIPLES.md` — operating rules; re-read every iteration
5. `CONVENTIONS.md` — code style
6. `RALPH.md` — loop wake-up prompt (also for the user)

## Remaining queue (high-level)

```
☑ Phase 1c       — ENABLE+FORCE RLS, migration c4f2e1b9a3d5 in prod
☑ Phase 1c-2     — apply_workspace_context primitive, cron per-ws iter,
                   /runs nested URLs, write_event self-applies, /me fix
☑ P1 Phase 2     — webhook lease refactor (covered by Phase 1c-2 cron)
☑ R2             — slug history triggers, migration d8a719c5b2e7 in prod
☑ Q part         — drop Sunset header + /healthz/deep done; D2 cursor
                   pagination still pending
☑ R4             — SA access helpers + HTTP API + CLI + 9 tests
□ P1 Phase 4     — cascade-down soft-delete + idempotency_keys table
□ R1             — audit auto-emission via SQLAlchemy listeners
                   (LOW PRIORITY — only 5 callsites today, hand-written
                   audit gives better action labels than auto-listener)
□ P3             — outbox table only (Procrastinate deferred)
□ Q D2           — cursor pagination on every list endpoint (~6-8h)
```

## Phase 1c-2 follow-up gaps still tracked

These were called out by the post-Phase-1c critic but partly mitigated by
later commits — leaving here as breadcrumbs for the next deep look:

- /healthz/deep webhook + stale checks read via the public engine and
  return false-low under FORCE RLS (no GUC set). Either add a privileged
  engine helper for the probe, or per-workspace aggregate.
- Trigger functions live in the `public` schema. Move to a dedicated
  `scryer_internal` schema for namespacing when convenient.

## Locked decisions (don't re-litigate)

Stack + foundations (from session 1; full detail in
`notepad/2026-05-02_session1_phases_build.md`):
- FastAPI + SQLAlchemy 2.x async + asyncpg + Postgres 17 (Neon) + R2
- Polymorphic FK = two nullable cols + CHECK + discriminator
- argon2id passwords; sha256 ApiKey/Invitation tokens; AES-GCM
  Credentials; pyjwt HS256 sessions
- Typer CLI, one file per noun in `cli/`; platformdirs for
  `credentials.json`
- `native_enum=False` (CHECK constraints) — not native PG enums
- Soft-delete via `archived_at` on every mutable resource

Redesign (from session 2; full detail in
`notepad/2026-05-03_session2_redesign.md`):
- **Procrastinate deferred** until ≥3 job types
- **Cascade-down soft-delete** (not restrict-on-children)
- **Finish ServiceAccount** as first-class principal (don't rip out)
- **Idempotency_keys table** folded into Phase 1c (RLS-aware day 1)
- **Outbox table-only** in Phase 3 (no Procrastinate library yet)
- **Cold-tier archive deferred** — see
  `notepad/2026-05-02_cold_tier_deferral.md`. Build trigger: PG > 50 GB
  OR storage line > $20/mo OR user requests > 2yr audit retention.
- **C0** (enum CHECK helper), **C2** (Trigger polymorphic FK), **D5
  trust_level** — DEFERRED per critic; gold-plating

## Per-session deep notepads

| File | Covers |
|---|---|
| `notepad/2026-05-02_session1_phases_build.md` | Original Phases 0-8 build (everything from project bootstrap through deployment) |
| `notepad/2026-05-02_cold_tier_deferral.md` | Cold-tier archive analysis + decision to defer (8 agents, cost math, build triggers) |
| `notepad/2026-05-02_cleanup_waves_0-8.md` | Post-build cleanup (Waves 0-6/7/8: surgical security/correctness fixes, R2 wiring, docs) |
| `notepad/2026-05-03_session2_redesign.md` | Pre-launch redesign push (16 commits: B0/B1, P1 Phases 1a-5, P2, R3, C1+C3, Phase 1c WIP, docs) |
| `notepad/phase_1c_checklist.md` | Active wave: 4 concrete items to ship RLS |

## Notepad discipline

- **Append, never delete.** Historical record is part of the state.
- **One file per session** — start a new `notepad/YYYY-MM-DD_<theme>.md`
  if working on a fresh theme; append to existing if continuing.
- **Update this file at wave boundaries** — current state, active wave,
  remaining queue, any new locked decision.
- **Per-wave detail goes in `notepad/<wave>_*.md`** — keep this file
  slim; future-you can grep across notepad/.
- **Index entries here** — when adding a new file under `notepad/`, add
  it to the table above with one line describing what it covers.

## When to stop the loop

Real blockers (write the ask in the active wave's checklist + exit):
- Credentials, API keys, connection strings the user must paste
- Destructive action requires user approval
- External infra needed (R2 bucket, env vars beyond docs)
- Choice between 2+ paths with undocumented tradeoffs

NOT blockers (just fix):
- Tests, mypy, lint, migration syntax errors → fix
- Schema/model drift → reconcile
- Agent output questionable → spawn another to vet
