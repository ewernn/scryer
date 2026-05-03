# Ralph-loop wake-up prompt for scryer

Paste the fenced block below into Claude Code (cwd = `~/code/scryer/`)
to resume the build. Designed for `/ralph-loop` continuous mode — each
iteration picks up from `scryer_notepad.md` and works one wave to
completion.

---

```
You are continuing the scryer pre-launch redesign. This is a /ralph-loop
iteration — keep working until you hit a REAL blocker (something that
genuinely requires the user). Trivial errors fix yourself. Don't ask
permission to continue. Don't stop "to checkpoint" or "give a status
update" — just keep working through the queue.

READ FIRST (in order, every iteration):

1. `cd /Users/ewern/code/scryer && git log --oneline -15`
   — what landed recently
2. `/Users/ewern/code/scryer/scryer_notepad.md`
   — RUNNING STATE: every wave done, every blocker, every locked
   decision. The "Phase 1c shipping checklist" + "Remaining queue"
   sections are the hot path.
3. `/Users/ewern/code/scryer/CONVENTIONS.md`
   — code style rules. Brevity is correctness. No ceremony.
4. `/Users/ewern/code/scryer/PRINCIPLES.md`
   — operating principles for this loop. Re-read every iteration.
5. `/Users/ewern/code/scryer/CLAUDE.md` (if it exists)
   — project-level instructions.

Then run TaskList to see in-flight work. Pick the highest-priority
pending wave per the notepad's "Remaining queue" section. If a wave is
in_progress and you're the same agent, continue it; if it's
in_progress and the previous run died mid-stream, audit what was done
via `git status` + `git diff` and either finish or revert cleanly.

WORKING PATTERN (follow exactly per substage):

A. Pick the next pending substage from notepad + TaskList.
B. SPAWN SUBAGENTS LIBERALLY. Unlimited usage; user explicitly wants
   this. Specifically:
   - For ANY non-trivial design question: 3-5 parallel investigators
     via `r:investigator` (different angles, run in parallel)
   - Before implementing a foundational change: critic agent via
     `r:critic` to stress-test the plan
   - After implementing a foundational change: spawn `r:verifier`
     and re-spawn `r:critic` to catch what you missed
   - Use `/z-scope-out` for any wave estimated >0.5d — it does
     parallel investigation + reflection automatically
   - Use `/z-double-check-agents` to vet recent agent work before
     trusting it
C. Implement per CONVENTIONS.md. NO shortcuts. NO duplicate code. NO
   ceremony comments. Single source of truth.
D. After every substage:
   - `make ci` (lint + format + mypy + pytest)
   - For migrations: `make test-migrations` (docker postgres + alembic
     up/down/up + smoke). MANDATORY before committing any migration.
   - Fix all failures before committing. Don't `--no-verify`.
   - Update `scryer_notepad.md` — append a brief note in the current
     session log entry. The notepad MUST stay current; future loop
     iterations depend on it.
   - `git add -A && git commit -m "<prefix>: <what>" && git push`
E. After every WAVE (a logical chunk = multiple substages): spawn
   `r:critic` + `r:verifier` in parallel to vet the wave output.
   Apply important findings before moving on.
F. Mid-iteration checkpointing: if you've been working >30 min within
   a single iteration without committing, STOP and commit your
   current state with a `wip:` prefix. The next loop iteration can
   resume from there.

REAL BLOCKERS (write the ask + pause; do NOT work around):
- User must paste credentials / API keys / connection strings
- User must approve a destructive action (drop table, force push,
  delete branch)
- User must provision external infra (R2 bucket, Railway env vars
  beyond what's documented in deployment.md)
- Two reasonable paths exist with different long-term tradeoffs and
  the choice isn't documented in the plan or notepad

NOT blockers (just fix):
- Test failure → debug + fix
- Mypy error → fix the type
- Lint error → fix or refactor
- Migration syntax error → fix it
- Missing field on a model → add it
- Agent's output is questionable → spawn another to vet

NEVER:
- Skip make ci or make test-migrations
- Commit with `--no-verify`
- Add `# TODO`, `# FIXME`, or `# HACK` comments. Either fix it now
  or open a task with `TaskCreate` and link from the notepad.
- Write placeholder code that "looks right but doesn't work"
- Apply a migration without `make test-migrations` validating it
- Touch `migrations/draft/` files without explicit user approval —
  those are parked WIP awaiting prerequisites
- Drop tests to make CI pass

LOCKED DECISIONS (don't re-litigate; from notepad):
- Procrastinate deferred until ≥3 job types
- Cascade-down soft-delete (not restrict-on-children)
- Finish ServiceAccount as first-class principal (don't rip out)
- Idempotency_keys table folded into Phase 1c (RLS-aware day 1)
- Outbox table-only in Phase 3 (no Procrastinate library yet)
- Cold-tier archive deferred (build trigger: PG > 50 GB OR storage
  line > $20/mo OR user requests > 2yr audit retention)
- C0 (enum CHECK helper), C2 (Trigger polymorphic FK),
  D5 trust_level — all DEFERRED per critic; gold-plating

Now: read the files in order, then continue.
```

---

## Notes for the user

- Ralph loop continues until a stop-promise is satisfied. Configure with
  `--max-iterations N` or `--completion-promise <text>` so it doesn't
  burn forever.
- All state persists across iterations via `scryer_notepad.md` + git.
  Auto-compaction discards in-conversation context but the notepad is
  read fresh every iteration.
- If the agent crashes mid-substage, the next iteration sees the dirty
  git tree via `git status` + decides cleanly via the working pattern.
