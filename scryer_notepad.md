# scryer notepad — INDEX

This is the loop's first read. Slim by design — historical detail lives
in `notepad/` per-session files. Update **this file** at every wave
boundary; update the relevant `notepad/*.md` for in-wave detail.

## Current state

- **Live**: <https://scryer-production.up.railway.app/api/v1/healthz>
  (`db_ok: true` after every push)
- **Migration head in prod**: `d8a719c5b2e7` (slug history triggers,
  shipped 2026-05-03)
- **Tests**: 169 passing in ~20s, includes 4 RLS enforcement tests via
  `rls_session` (scryer_app role, RLS-gated)
- **Active wave**: pick next from the queue below

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
□ P1 Phase 4     — cascade-down soft-delete + idempotency_keys table
□ R1             — audit auto-emission via SQLAlchemy listeners
                   (LOW PRIORITY — only 5 callsites today, hand-written
                   audit gives better action labels than auto-listener)
□ R4             — finish ServiceAccount as first-class principal
                   (extend assert_workspace_member, CLI command,
                   end-to-end test)
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
