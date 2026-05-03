# scryer notepad — INDEX

This is the loop's first read. Slim by design — historical detail lives
in `notepad/` per-session files. Update **this file** at every wave
boundary; update the relevant `notepad/*.md` for in-wave detail.

## Current state

- **Live**: <https://scryer-production.up.railway.app/api/v1/healthz>
  (`db_ok: true` after every push)
- **Migration head in prod**: `8f52f0aae3fe` (cascade fixups: extend
  grandchildren + trigger save/restore include_archived). Recent:
  47ae45c7f465 (Run.executor_pid), 9db64f8a534b (cascade-down soft-delete
  + archived_at RLS), a1b2c3d4e5f6 (idempotency_keys), f3c5d8e0a712
  (time-as-DB-truth), e9f1a4c8b3d6 (audit_events strict isolation),
  d8a719c5b2e7 (slug history triggers), c4f2e1b9a3d5 (Phase 1c FORCE RLS).
- **Tests**: 201 passing in ~26s
- **Active wave**: NONE — pre-launch hardening complete; remaining queue
  is deferred (R2 GC, cursor pagination, outbox)

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

## Wave 2+5 SHIPPED (2026-05-03 morning)

Cascade-down soft-delete from workspaces + archived_at-as-RLS-predicate
in migration `9db64f8a534b`. 9 tests in test_archive_workspace.py.
DELETE /workspaces/{slug} endpoint owner-only + idempotent.

**Key learning, recorded for future migrations:** PG evaluates the
SELECT/USING expression against the *post-row* of every UPDATE — not
just WITH CHECK. The cascade trigger sets `app.include_archived='true'`
at function entry so its UPDATE-archive cascade UPDATEs pass the
post-row visibility check (otherwise USING with `archived_at IS NULL OR
include_archived='true'` rejects archived NEW rows). Two-part fix:
USING/WITH CHECK split + include_archived inside trigger. Service code
(archive_workspace) also sets include_archived=true via
apply_workspace_context for defense-in-depth.

## Critic-followup fixes SHIPPED (2026-05-03 noon)

After r:critic review of Wave 2+5+3+idempotency, fixed 5 HIGH severity
findings:

1. **Cascade was 1-hop only** — versioned grandchildren (datasets,
   scorers, agents, tools, prompts, tasks) stayed visible after archive.
   Migration 8f52f0aae3fe extends the cascade list to all 11 children.
2. **Trigger include_archived GUC leaked into post-trigger statements**
   in same txn — same migration adds save/restore wrapper around the
   set_config call.
3. **archive_workspace didn't terminate local procs** — service now
   iterates _active_procs for the workspace's in-flight Runs and runs
   the same SIGTERM/grace/SIGKILL sequence as cancel_run.
4. **executor_pid finally race on unhandled exceptions** — execute_run
   now wraps the for-loop in `except Exception: crashed=True; raise`
   and the finally block stamps status='failed' + executor_crashed
   reason BEFORE the exception propagates. No more 'running' Runs
   waiting for heartbeat reaper to mislabel as 'heartbeat_timeout'.
5. **One-shot secrets cached in idempotency response** — webhook secret
   and SA full_key. Added `cache_body: bool = True` kwarg to
   capture_idempotency_response; flipped to False on those two
   endpoints. Replays now return cached status_code with NULL body —
   client can't recover the secret, must re-issue with fresh key.

Plus 2 LOW: cancel_run wraps proc.terminate() in try/except for
ProcessLookupError race, and added comment to migration downgrade.
Tests for crash-handling + grandchild cascade added.

## Wave 3 SHIPPED (2026-05-03 morning)

PID-tracking cancellation. Migration `47ae45c7f465` adds
runs.executor_pid (nullable int). services/runs.py gains module-level
_active_procs dict (run_id → asyncio.subprocess.Process). execute_run
sets executor_pid = os.getpid() in the queued→running atomic claim,
registers proc via sandbox.run_user_code's new on_proc_start callback,
and clears in finally. cancel_run flips status, then if executor_pid
matches local PID, terminates the proc (proc.terminate() + 5s grace +
proc.kill()). Cross-process cancellations land at the next per-record
cooperative status check. 6 tests in test_run_cancellation.py prove
queued cancel, done-cancel-conflict, executor_pid lifecycle, same-PID
proc termination, cooperative cross-process cancel, and registry
cleanup. 200 passing total.

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
