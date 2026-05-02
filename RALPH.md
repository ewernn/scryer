# Ralph-wiggum wake-up prompt

Paste the fenced block below into Claude Code (cwd should be `~/code/scryer/`)
to resume the build. The agent should drive Phases 1 → 8 to completion unless
it hits a real blocker.

---

```
You are continuing the scryer build. Read these in order before acting:

1. /Users/ewern/Desktop/code/trait-stuff/traitinterp/docs/other/may2_scryer_plan.md
   (DESIGN PLAN — source of truth; do NOT re-litigate decisions)
2. /Users/ewern/code/scryer/scryer_notepad.md
   (RUNNING NOTEPAD — every locked decision, every applied fix, every phase status)
3. /Users/ewern/code/scryer/CONVENTIONS.md
   (CODE STYLE — user HATES: duplicate code, verbose code, long files,
   ceremony comments. Brevity is correctness.)
4. Run `cd ~/code/scryer && git log --oneline -10` to see recent commits

Your job: Drive scryer through Phases 1-8 of plan §17 to completion. Do NOT
stop after one phase. Do NOT ask for permission to continue. Only stop on a
REAL blocker (something that genuinely needs the user — credentials they
must paste, infra they must provision). Trivial errors fix yourself.

Working pattern (follow exactly):

A. Pick the next pending substage from the task list (TaskList) or scryer_notepad.md.
B. SPAWN MULTIPLE BACKGROUND SUBAGENTS LIBERALLY to research, critique, verify.
   Spawn 3-5 in parallel for any non-trivial design question. The user has
   unlimited Claude Code usage and explicitly wants this.
C. Implement the substage. Follow CONVENTIONS.md religiously.
D. After every substage:
   - run `make ci` (lint + format check + mypy + pytest)
   - if anything fails: fix it before committing
   - update scryer_notepad.md with what was done + any deviations
   - `cd ~/code/scryer && git add -A && git commit -m "Phase X.Y: ..." && git push`
E. After every PHASE: spawn `r:critic` subagent to stress-test the phase output.
   Apply important critic findings before moving on.
F. If you hit a blocker requiring the user, write the ask clearly and pause.
   Do NOT work around with placeholders or mocks.

Decisions ALREADY locked (don't re-decide):
- Stack: FastAPI + SQLAlchemy 2.x async + asyncpg + Postgres 17 (Neon) + R2
- Polymorphic FK = two nullable cols + CHECK + discriminator
- argon2-cffi for passwords; sha256 for ApiKey + Invitation tokens
- AES-GCM for Credentials (cryptography lib, 12-byte nonce, base64)
- pyjwt HS256 for sessions
- fastapi-problem-details + asgi-correlation-id for RFC 9457 + X-Request-ID
- Typer for CLI; one file per noun in cli/; platformdirs for credentials.json
- Tests: savepoint rollback per-test, schema-isolated test_<random> namespace
- Native enums replaced with native_enum=False (CHECK constraints)
- Project visibility hybrid (private|workspace) per plan §5
- Server-side execution (subprocess sandbox per plan §7)
- Soft-delete via archived_at on every mutable resource
- Naming convention on Base.metadata for clean Alembic autogen names

Deferred to v1 (don't add unless plan changes):
- Approval, Plan, Hash chain on AuditEvent, claimed_by/until,
  Run.baseline_run_id, agent_feed, MCP curation, public share links

Current phase status (refresh from scryer_notepad.md, this list ages):
- Phase 0: DONE
- Phase 1.1-1.4: DONE (schema + migration applied to Neon)
- Phase 1.5+: Continue from here

Remember: SPAM SUBAGENTS. The user explicitly asked for this. Spawn parallel
investigators for any decision, parallel critics at every phase boundary,
parallel verifiers to double-check your work. Unlimited usage.

Now: read the plan + notepad + conventions, then continue.
```

---

## Notes for the user

- This prompt assumes Claude Code is in `~/code/scryer/`. If it's elsewhere,
  the agent will figure it out from the absolute paths.
- If the build hits a HARD blocker, the agent will pause and write to you;
  paste the message back when you're awake.
- All work is committed and pushed after each substage so nothing is lost
  even if the agent crashes mid-stream.
- The notepad is the canonical state. The plan is the strategic source of truth.
