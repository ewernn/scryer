# Documentation Update Guidelines

**Core Principles:**
- **Delete first, add second** — always remove outdated content before adding new
- **Present tense only** — document what IS, not what WAS
- **Zero history** — no changelogs, migration notes, or "previously" references
- **YAGNI for docs** — delete unused files and sections immediately

**When to Delete:**
- Old implementations, fixed bugs, previous versions
- Historical context and migration notes
- Unused files or features
- Anything you can't verify still works

**File Management:**
- Delete entire unused documentation files
- Remove orphaned sections that no longer apply
- Clean up broken internal links to deleted content

**File placement (scryer):**
- `docs/main.md` — navigation hub, ≤500 lines
- `CLAUDE.md` — Claude-specific behavior; imports `@docs/main.md`
- `scryer_notepad.md` — running build journal (NOT user docs)
- `CONVENTIONS.md` — code style rules
- `RALPH.md` — wake-up prompt for resuming builds

Per-noun deep-dives go in `docs/{noun}.md` only when 50+ lines of explanation
are warranted.
