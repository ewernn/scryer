You are continuing the scryer pre-launch redesign in a ralph loop iteration keep working until you hit a REAL blocker something that genuinely requires the user trivial errors fix yourself do not ask permission to continue do not stop to checkpoint or give a status update just keep working through the queue READ FIRST in order every iteration first chdir into the scryer repo at home ewern code scryer then run git log oneline of last 15 commits to see what landed recently then read the file scryer_notepad dot md at repo root which holds the RUNNING STATE every wave done every blocker every locked decision the Phase 1c shipping checklist and Remaining queue sections are the hot path then read CONVENTIONS dot md at repo root for code style rules brevity is correctness no ceremony then read PRINCIPLES dot md at repo root for operating principles re read every iteration then read CLAUDE dot md if it exists for project level instructions then run TaskList to see in flight work pick the highest priority pending wave per the notepads Remaining queue section if a wave is in progress and you are the same agent continue it if it is in progress and the previous run died mid stream audit what was done via git status and git diff and either finish or revert cleanly WORKING PATTERN follow exactly per substage A pick the next pending substage from notepad and TaskList B SPAWN SUBAGENTS LIBERALLY unlimited usage user explicitly wants this for any non trivial design question spawn 3 to 5 parallel investigators via r investigator with different angles run in parallel before implementing a foundational change spawn a critic agent via r critic to stress test the plan after implementing a foundational change spawn r verifier and re spawn r critic to catch what you missed use the z scope out skill for any wave estimated above half a day it does parallel investigation and reflection automatically use the z double check agents skill to vet recent agent work before trusting it C implement per CONVENTIONS dot md no shortcuts no duplicate code no ceremony comments single source of truth D after every substage run make ci which does lint format mypy and pytest for migrations run make test migrations which uses docker postgres and alembic up down up plus smoke MANDATORY before committing any migration fix all failures before committing do NOT use no verify update scryer_notepad dot md append a brief note in the current session log entry the notepad MUST stay current future loop iterations depend on it then git add dash A and git commit with a prefixed message and git push E after every WAVE a logical chunk equals multiple substages spawn r critic and r verifier in parallel to vet the wave output apply important findings before moving on F mid iteration checkpointing if you have been working over 30 min within a single iteration without committing STOP and commit your current state with a wip prefix the next loop iteration can resume from there REAL BLOCKERS write the ask and pause do NOT work around them user must paste credentials or API keys or connection strings user must approve a destructive action like drop table force push or delete branch user must provision external infra like R2 bucket or Railway env vars beyond what is documented in deployment dot md two reasonable paths exist with different long term tradeoffs and the choice is not documented in the plan or notepad NOT blockers just fix test failure debug and fix mypy error fix the type lint error fix or refactor migration syntax error fix it missing field on a model add it agents output is questionable spawn another to vet NEVER skip make ci or make test migrations never commit with no verify never add TODO FIXME or HACK comments either fix it now or open a task with TaskCreate and link from the notepad never write placeholder code that looks right but does not work never apply a migration without make test migrations validating it never touch migrations slash draft slash files without explicit user approval those are parked WIP awaiting prerequisites never drop tests to make CI pass LOCKED DECISIONS do not re litigate from notepad Procrastinate deferred until at least 3 job types cascade down soft delete not restrict on children finish ServiceAccount as first class principal do not rip out idempotency_keys table folded into Phase 1c RLS aware day 1 outbox table only in Phase 3 no Procrastinate library yet cold tier archive deferred build trigger is when PG cumulative storage exceeds 50 GB OR storage line item exceeds 20 dollars per month OR user requests over 2 years audit retention C0 enum CHECK helper C2 Trigger polymorphic FK and D5 trust_level all DEFERRED per critic gold plating now read the files in order then continue

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
