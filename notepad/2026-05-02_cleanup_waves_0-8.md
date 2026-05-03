## 2026-05-02 — Cleanup execution: Waves 0-6 (124 tests passing)

Critic-driven scope after cold-tier deferral. Bug-fix list ordered by risk × cost.
All commits to main, Railway auto-deployed. Test count went 106 → 124.

### Wave 0
- Cold-tier archive deferral locked in §15 #20 + this notepad.
- webhook backoff `[d.attempts]` (was `[d.attempts + 1]` — skipped 1s slot).

### Wave 1 — surgical security/correctness
- Timing leak in `authenticate()`: dummy argon2 verify on user-not-found
  (defeats email enumeration via response time). New test floors at 10ms.
- Reaper SKIP LOCKED: `with_for_update(skip_locked=True)` on `reap_stale_runs`
  (matches `dispatch_due_triggers` and `deliver_pending` pattern).
- Rate limit: dual email + IP keying (auth.py was email-only — bypassable),
  bounded growth via dead-key eviction, threading.Lock for multi-worker safety.
  Email cap 5/60s; IP cap 50/60s (CGNAT/NAT reality). `_reset_for_tests()`
  called from `client` fixture so tests don't burn each other's IP budget.
- Web run page pagination: `page` + `per_page` query params (was always
  loading 1000 rows inline + JSONB into Jinja).
- Results index swap: drop `ix_results_run_id_score_value` (wrong sort
  column), add `ix_results_run_id_record_id WHERE invalidated_at IS NULL`
  matching `list_results` `ORDER BY record_id`.

### Wave 2 — middleware
- `ApiVersionHeadersMiddleware` rewritten as pure ASGI (was BaseHTTPMiddleware
  which buffers entire request body — Starlette known issue).
- New `BodySizeLimitMiddleware`: 10 MiB cap, fast-path on Content-Length,
  streaming-path wraps `receive()` to count chunks, sends RFC 9457 problem+json
  on rejection. Outermost middleware so oversize requests get rejected before
  any other middleware processes them.

### Wave 3 — webhook end-to-end
- Built webhook CRUD from scratch (no router existed): WebhookOut omits
  secret, WebhookCreatedOut adds it (returned only on POST + rotate).
  Owner role required for write ops via new `assert_workspace_role` helper.
  All ops audited (secret auto-redacted by audit `_REDACT_KEYS`).
- Wired `fire_event` from `execute_run` on completion — was dead code
  (critic flag #7). Event type = `run.{status}`.
- `deliver_pending` refactored to lease pattern: SKIP LOCKED claims batch,
  bumps `next_attempt_at` by `_LEASE_SECONDS=120`, COMMITs to release row
  locks, then HTTP outside any held lock. If worker crashes, lease expires
  and another worker resumes.

### Wave 4
- a: PYTHONHASHSEED=0 + PYTHONDONTWRITEBYTECODE=1 in sandbox env (determinism +
  no stale .pyc surprises).
- c: `queue_run(supersede=True)` atomically marks prior queued/running Runs
  of the same Task as `superseded` (must set `superseded_by_run_id` to satisfy
  CHECK `ck_runs_superseded_coherent`). API: RunStartRequest.supersede;
  CLI: `scryer run start --supersede`.
- b: CSRF protection on /web/* POST — IN PROGRESS (only auth POST is logout).

### Wave 6 — onboarding
- POST /api/v1/workspaces/{slug}/invitations — owner-only, token returned ONCE.
- POST /api/v1/auth/signup — token redeem (race-safe via UPDATE ... WHERE
  used_at IS NULL ... RETURNING), issues JWT in response.
- `scryer invite create / redeem` CLI commands.
- `scryer task push / list / get` CLI (existing API).

### Pending — needs user action
- **Wave 5 BLOCKED**: dropping `server_executable` from Scorer.content_hash
  invalidates ALL existing scorer hashes. User decision needed: (a) backfill
  hashes pre-drop, (b) accept churn (re-version everything), (c) leave field,
  just stop using it. NOT YET IMPLEMENTED.
- **Wave 7**: R2 wiring (Balanced split). Critic flag #8 corrected: column
  to spill is `TraceStep.payload_json` NOT `Trace.payload_json`. Needs user
  to provision R2 bucket + add R2_* env vars to Railway. Estimate 8-12h
  (first-time R2 wiring).
- **Wave 8**: Docs push (main, architecture, api, cli, deployment) per
  docs/doc-update-guidelines.md. ~3-4h.
- **Wave 51**: tests/test_concurrency.py 2 failures investigation. Lease
  pattern logic looks correct but test still reports 24 (= 8×3) handled.
  Suspect test setup oddity (asyncio.gather + connection pool interaction).
  Not blocking prod (Cron runs deliver_pending once/min, not concurrently).

---

