# Memory / CLAUDE.md rehaul — what is still open

Last updated 2026-09-01. The session that did most of this work (`1f5e732a`,
Lorenzo account, 21:32–23:29) hit its Claude session limit **mid-`Edit`**, in the
middle of the last item it started; this note reconstructs where it stopped, from
that session's transcript, and tracks what is finished against what is not.

Companion: `notes/memory-ui-research.md` (the cited research the restructure
applies), `notes/memory-tabs-rehaul.md` (the original brief).

---

## 0. State of the tree

`main` is at `acbbee1`. The rehaul is **uncommitted** in the working tree, plus
two new untracked tests (`tests/test_gui_memory_surface.py`) and these notes.

Verified 2026-09-01 after the bucket restructure:

- `pytest tests/` — **1698 passed, 1 skipped**
- `ruff check --select E9,F821,F811,F632,F702,B023,B006,B002` — clean
- `py tools/smoke_gui.py` — **150 checks**, FAILURES none, JS errors none
- `py tools/shot_gui.py` — every page and all 9 project tabs clean, all 7 skins.
  `RAGGED` on the per-skin audit is the pre-existing dashboard row, unchanged.

One caveat on the suite: a run taken while auto-memory happened to be rewriting
`.claude/rules/*.md` and `CLAUDE.md` produced **8 sandbox teardown errors** from
`_no_writes_outside_the_sandbox`. They did not reproduce on a quiet run. The
harness already documents this race for its polling threads; live background
writes are a second source of it.

---

## 1. Still open

### AGENTS.md (research disagreement E)
AGENTS.md is the cross-tool standard Claude Code deliberately does not read; it
recommends `@AGENTS.md` as CLAUDE.md's first line instead. If claudectl's
generated blocks live only in CLAUDE.md, a repo that later adopts AGENTS.md has
them in the file other agents ignore. **A decision to take, not necessarily a
change.**

---

## 2. Landed since the handoff

### `updated <n> ago` on each machine-written row (P1 #10) — done
It was called "blocked on data", and that was the wrong reading: the data was
already on disk. The answer is each artifact's own **mtime** (`memhub.last_written`,
on the wire as `written`), not a stamp written into the payload — a stamp is a
second thing to keep in step with the write, and one `save()` that forgets it
makes the row lie confidently, whereas an mtime cannot disagree with the file.

Only artifacts that ARE exactly one file are dated. A CLAUDE.md block shares a
file with your own prose, so dating it off that file would call the digest fresh
because you fixed a typo — those rows stay bare and the build time is stated
once, on the `Always on` group, which is what this note originally proposed as
the fallback. Gate: `test_only_an_artifact_that_is_one_file_gets_dated`.

### The generated-block notice (P3 #15–17) — done, with no migration
The migration this note feared is avoided entirely by putting the notice on the
line *after* the opener rather than inside it. Every writer already rewrites the
block's content, so an old CLAUDE.md gains the notice on its next write and the
~20 readers that match the opener as a literal string are untouched.
`config.generated_note(source, how)` is the one wording — tool name, prohibition,
route back — and `test_every_generated_block_says_who_wrote_it_and_how_to_rebuild`
makes it structural: a module that opens a machine block must also write a notice.

### Two blocks were being reported as your own prose
Not in the research, found while making the tab honest. `ctxaudit.split_blocks`
knew three sentinels; `CLAUDECTL:AGENTS` and `CLAUDECTL:LOOP` fell into
`manual`, so the token audit charged them to your prose, the CLAUDE.md tab
labelled them *"yours — claudectl never rewrites it unprompted"* while claudectl
rewrote them on every agent change, and AI compression was handed claudectl's
own generated tables to reword. Splitting them out required carrying them across
a rewrite in the same change (`_preserve_machine_blocks`), or compress would
have deleted them — the two halves are one change, and one gate each.

### The bucket restructure (the thing that was interrupted)
The interrupted edit had rewritten `invRow` from six parameters to five and added
`bucket()` / `invTable()` / `REACH` / `rch()`, but died before rewriting the
eleven call sites. They kept passing the old sixth argument, so the load-rule
keyword rendered in the **size** column, the size rendered where the button goes,
and the action was **dropped** — with the whole suite, the smoke tool and the
overflow audit all green, because every check read a row's *text* and text does
not know which cell it is in.

The twelve artifacts are now four groups, stated once each rather than as a badge
repeated down a wall:

| bucket | reach | members |
|---|---|---|
| Always on | every session | CLAUDE.md digest, AUTOGEN / SESSIONS, cross-project conventions, recent work |
| Every prompt | every prompt | Recall (renamed from "per-prompt recall injection" — a load rule masquerading as a topic) |
| When relevant | when Claude opens a matching file | path-scoped rules, lessons |
| Stored, not loaded | on request | semantic graph, reinforcement log, edit log, workspace manifest, version snapshots — collapsed behind *"5 records · 0 tokens — read only when you or Claude ask"* |

Deviations from the research's proposed assignment, both deliberate:
- It put **path-scoped rules** in *Always on* with a "verify this" caveat, because
  the files it could see had no `paths:`. That was fixed first (3 875 tok/turn),
  so they genuinely belong in *When relevant*.
- It put **cross-project conventions** and **recent work** in *Stored*. Both are
  really injected — conventions into the global CLAUDE.md, recent work at
  SessionStart — so both sit in *Always on*.

Also landed from the research: the ownership sentence (P0 #1) and the always-on
cost as a **share of the context window** (P1 #7) — `CTX_WINDOW` had been defined
and unused since the interrupted edit added it.

### The gate that would have caught it
`test_no_row_helper_is_called_with_more_arguments_than_it_takes` counts the
top-level arguments at every call of `invRow` / `bucket` / `invTable` / `rch` and
fails when a call passes more than the function declares — JS discards the extra
silently. Mutation-verified against the exact historical shape (a sixth argument
on one `invRow`). Fewer than declared stays legal, since trailing arguments are
genuinely optional. Line numbering in the failure is real, because `_source()`
now collapses a block comment to the newlines it spanned.

### `linguist-generated`
`.gitattributes` marks `.claude/rules/claudectl-mem-*.md`, `docs/api.md` and
`plugin/skills/**` as generated, so their diffs collapse in review. Verified with
`git check-attr`.

### The live CLAUDE.md
Regenerated through `claude_md.prune_claude_md` (atomic, snapshotted, manifest
re-baselined). The SESSIONS block held ten rows of claudectl talking to itself
and now holds ten real topics. One opener was missing from `HEADLESS_OPENERS` —
`agents.sharpen_prompt` — so it leaked one row; added, and `agents` is now in the
gate's module list. The gate can only rot one way and this was the other way, so
its docstring now says why that is acceptable: `HEADLESS_MARK` covers every new
call at the `memory._claude_stdin` seam, leaving the opener list to matter only
for transcripts written before it existed.

---

## 3. Finished earlier — do not redo

Each was verified against this repo's real data, per *"test on my real claudectl
project before saying it's implemented correctly"*.

- **Why the cycle said "0 modules, 6 still queued".** All six `_extract` calls
  **failed** (rate limit); `failed_units` and `skipped_units` were summed into one
  `pending_units` and every consumer worded it "the next cycle takes them", so six
  dead calls an hour read as progress. `gui_api._run_cancellable` also discarded
  the real error on a nonzero exit when there was no job context — which is
  exactly the scheduler and the detached worker.
- **"why does the reinforcement log say folding in?"** It was a literal string
  derived from the graph's top-hits list — a different thing, and it never
  changed. `recall.hits_pending` counts the sidecar; the row reads `<n> to fold in`.
- **The rule files were never actually lazy.** All 11 `claudectl-mem-*.md` used
  `globs:` (the Cursor key). Claude Code reads `paths:`; a rule without it "is
  loaded unconditionally". Measured: **22 238 → 18 363 always-on tokens, 3 875
  off every single turn**, 11 files migrated free, and one rule that had scoped
  itself narrower than its own module corrected.
- **The CLAUDE.md memory block carries content, not just an index** — the budget
  goes to the highest-signal lessons and the module dependency edges instead of a
  module listing.
- **Most-reinforced facts are clickable** (`entDetail`).
- **History is no longer a wall, and sits beside the artifact it is history of.**
- **Headless sessions are marked at the one seam** (`memory._claude_stdin`).
- **Every field the four memory handlers put on the wire reaches the screen**,
  enforced by `tests/test_gui_memory_surface.py`.
