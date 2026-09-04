---
title: What belongs in CLAUDE.md, and what belongs in path-scoped rules
description: "CLAUDE.md is read on every turn; path-scoped rules cost nothing until Claude opens a matching file. A working split, block by block, with token costs."
date: 2026-09-01
tags: [claude-code, claude-md, context, conventions]
author: Babar Muhammad Anas
faq:
  - q: What should go in CLAUDE.md?
    a: Only what is true for the whole project and needed on almost every turn — the build and test commands, the conventions Claude keeps getting wrong, hard constraints, and a short pointer to where deeper knowledge lives. Anything that applies to one module, one language or one directory belongs in a path-scoped rules file instead, because CLAUDE.md is re-read on every single message.
  - q: What is a .claude/rules file and how is it different from CLAUDE.md?
    a: A rules file is a markdown file under `.claude/rules/` that can carry a `globs:` header scoping it to specific paths. Claude Code loads it only when it touches a matching file, so per-module detail sits on disk at zero cost until it becomes relevant, whereas CLAUDE.md is loaded on every turn regardless of what you are working on.
  - q: How many CLAUDE.md files does Claude Code load?
    a: Several, and they stack — the user-level `~/.claude/CLAUDE.md` that applies to every project on the account, the project's own `CLAUDE.md`, one inside `.claude/`, and a local variant. Each can pull in more files with `@import`, and an import pointing at a file that no longer exists loads nothing and says nothing, which is a common source of silently missing context.
  - q: Should I put my file tree or module list in CLAUDE.md?
    a: Generally no. A hand-maintained copy of something the code already states goes stale the week after you write it, and a stale file tree is worse than none because the model trusts it. Prefer a generated block clearly marked as generated, or a pointer to a command that produces the answer on demand.
  - q: How long should CLAUDE.md be?
    a: Short enough that you can justify paying for it on every message. A practical target is under about 500 tokens of hand-written prose plus a bounded generated index; past roughly 200 lines the file is usually carrying per-module detail that belongs in a path-scoped rules file.
---

## The short answer

`CLAUDE.md` is read on every turn, so it should hold only what is true for the whole project and needed almost always: build and test commands, the conventions Claude keeps violating, hard constraints, and a pointer to where deeper knowledge lives. Everything scoped to one module, one language or one directory belongs in a `.claude/rules/*.md` file with a `globs:` header, which Claude Code loads only when it touches a matching path — several kilobytes of detail at zero cost until it is relevant. The split is not stylistic. It is the difference between a context cost that grows with your codebase and one that does not.

## The loading model

Before deciding what goes where, know what actually gets read. Claude Code stacks several instruction files:

| File | Scope |
|---|---|
| `~/.claude/CLAUDE.md` | Every session on that account, every project |
| `<project>/CLAUDE.md` | The project |
| `<project>/.claude/CLAUDE.md` | The project |
| Local variant | The project, not committed |
| `.claude/rules/*.md` | Path-scoped when they carry `globs:`, otherwise always-on |

Each can pull in more with `@import`. The failure mode there is quiet: an `@import` pointing at a file that no longer exists loads nothing and reports nothing. claudectl's memory map (`M` in the sessions menu, or the CLAUDE.md tab in the GUI) lists which files load for a project and flags broken imports as **missing**, which is the only way to find them short of reading each one.

The account-level file deserves particular suspicion, because it costs you on every project. claudectl uses it for MCP tool documentation, written in per-server sentinel blocks so re-running the analysis updates one server's section and leaves the rest alone:

```
<!-- MCP:Notion:START -->
## MCP: Notion
… tool listing …
<!-- MCP:Notion:END -->
```

If you have a convention that only matters in one repo, it does not belong there.

## The decision rule

For each thing you want Claude to know, ask two questions: **is it true everywhere in this project**, and **is it needed on most turns**? Two yeses means `CLAUDE.md`. Otherwise, further out.

| Content | Where | Why |
|---|---|---|
| Build, test, lint commands | `CLAUDE.md` | Needed constantly, project-wide, tiny |
| "This machine uses PowerShell 5.1, no `&&`" | `~/.claude/CLAUDE.md` | True on every project on this machine |
| Hard constraints ("never rewrite `settings.json` wholesale") | `CLAUDE.md` | Cheap, and the cost of violating it is high |
| Per-module architecture, entities, relations | `.claude/rules/` with `globs:` | Only relevant when that module is open |
| "React components use the `use-` prefix" | `.claude/rules/` scoped to `src/components/**` | Language- and directory-specific |
| A workflow with steps ("how to cut a release") | `.claude/skills/<name>/SKILL.md` | Loaded on demand, by name |
| The file tree | Nowhere hand-written | Goes stale; generate it or let the model look |
| API endpoint catalogue | Generated, or nowhere | Same |

The last two rows are a lesson learned the expensive way. A hand-maintained document in this repo catalogued seventeen HTTP routes that had never existed and design problems that had since been fixed. It was deleted; the equivalent is now generated from the route tables themselves, with a test that fails when it is stale. **A hand-maintained copy of something the code already states will be wrong**, and a wrong file tree is worse than no file tree because the model trusts it.

## What a good CLAUDE.md entry looks like

The entries that earn their tokens are not style preferences. They are the gotchas a competent agent would otherwise rediscover, written with the *reason* attached, because a rule without a reason gets rationalised away in edge cases.

A real one from this project:

> **Only `paint()`/`paintNow()` may write `#content` — a convention in 15 call sites is not a guarantee.** Almost every renderer paints a spinner, awaits a fetch, then writes the real markup; nothing stopped that second write landing after you had navigated away, so a slow page overwrote the one you were on. The guard is now inside the two functions themselves. `test_no_page_can_paint_over_the_one_you_are_on` counts the raw writes and fails at three.

Three things make that worth its tokens. It names the symptom, so the model recognises the situation. It names the root cause, so the model does not fix it in the wrong place. And it names the **test that enforces it**, which converts a request into a verifiable constraint — the model can run it.

The counter-example is a paragraph of adjectives: "we care about clean, maintainable code". Zero information per token.

The Bash heredoc entry is another good shape — it exists purely because the failure is invisible:

> **The Bash tool's heredoc eats backslashes; use `Write` for patch scripts.** `py - <<'PY'` collapsed `\\n` to a literal newline twice in one session, once producing an unterminated string literal and once silently failing an `assert` so a patch was lost.

You would not guess that from first principles, and the model will hit it again next week.

## Path-scoped rules in practice

A rules file is markdown with a header that scopes it:

```markdown
---
globs: "claude_sessions/memory*.py"
---
# Memory subsystem
- The graph lives at `.claudectl/memory/graph.json`.
- Consolidation merges duplicate entities across modules before the
  importance cap evicts anything.
- Never write the graph without going through the atomic helper.
```

Loaded when Claude opens a matching file; absent otherwise. This is where per-module knowledge belongs, and claudectl generates one per module from its memory graph — entities, their types, one-line summaries, and the relations between them:

```
.claude/rules/claudectl-mem-<project>-<module>.md
```

The one thing to check on your own hand-written rules: **a rules file with no `globs:` is always-on**, and will be sitting in the same cost column as `CLAUDE.md` where you did not expect it. claudectl's context weight audit marks each rule *lazy* or not, which is the fastest way to catch one.

## CLAUDE.md as blocks, not as text

Once a file mixes your prose with generated content, treating it as one blob makes every regeneration a risk. Structure it as blocks with sentinels:

| Block | Written by | Regenerated by |
|---|---|---|
| Your prose | You | Nothing |
| `<!-- CLAUDECTL:KEEP:START/END -->` | You, fenced | Nothing, ever |
| `<!-- AUTOGEN:START/END -->` | Scaffold — git repos, recent commits, README heads | Scaffold |
| `<!-- SESSIONS:START/END -->` | Session topics | Prune |
| `<!-- CLAUDECTL:MEMORY:START/END -->` | The memory digest | Memory build |

The GUI's CLAUDE.md tab shows the file as exactly this — each block with its own token cost and the one button that regenerates that block alone. Pruning the session log does not touch your prose; rebuilding the memory digest does not touch the autogen block.

The KEEP fence is the strongest of these. AI compression does not merely *avoid rewriting* a fenced section, it never sends it to the model at all, so it cannot be reworded, shortened or dropped. Use it for anything whose exact wording matters.

## The two blocks that grow forever

If your `CLAUDE.md` is generated in part, two blocks are unbounded by nature: the session-topics log (one line per session, nothing ever removed) and the commit list. Cap both — `claude_md_sessions_cap` defaults to 10 recent entries, `claude_md_commits` sets the commit count. This is the single most common cause of a file that was 60 lines in March and 300 in August without anyone writing a word.

The cost side of all this — measuring the always-on total, compression, deny rules — is covered in [the 250-token index pattern](/blog/cut-claude-code-token-costs).

## Compact instructions

One section that is worth its per-turn cost on any long-running project: `# Compact instructions`, which steers what Claude Code's auto-compaction keeps when the context window fills. It costs a handful of tokens per turn and changes what you still have on the other side of a compact. claudectl's scaffolded and AI-generated files include one, and the audit offers to add it when missing.

## A shape that works

```markdown
# Project

## Commands
build: `npm run build` · test: `npm test` · lint: `npm run lint`

## Constraints
- Never edit `db/schema.sql` by hand; migrations only (`npm run migrate:new`).
- All money values are integer cents. `test_no_float_money` enforces it.

## Gotchas
- **The worker pool must be drained before exit.** …symptom, cause, guard…

## Compact instructions
Keep: the current task, file paths touched, decisions made. Drop: tool output.

<!-- CLAUDECTL:MEMORY:START -->
…bounded generated index: repos, modules, a recall pointer…
<!-- CLAUDECTL:MEMORY:END -->
```

Everything else — per-module detail, per-language conventions, architecture — lives in `.claude/rules/` and costs nothing until Claude opens the file it describes.

Reference: [docs.claudectl.space/reference](https://docs.claudectl.space/reference/) for the block sentinels and generation behaviour, [docs.claudectl.space/memory](https://docs.claudectl.space/memory/) for the rules generator.
