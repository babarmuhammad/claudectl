---
title: Why Claude Code forgets your project between sessions, and what to do about it
description: Claude Code starts every session with an empty context window. Here is the actual mechanism behind the amnesia, and the three surfaces that fix it.
date: 2026-09-01
tags: [claude-code, memory, context]
author: Babar Muhammad Anas
faq:
  - q: Why does Claude Code forget everything when I start a new session?
    a: Because a Claude Code session is its own memory. Everything the model knows about your project lives in one context window that is discarded when the session ends, and the transcript written to disk is an append-only log that nothing ever re-reads. The only knowledge that survives into the next session is what gets loaded at startup — CLAUDE.md, path-scoped rules files, and anything a SessionStart hook injects.
  - q: Does /compact preserve my project context?
    a: No. `/compact` summarises the conversation to free room in the context window, and the detail it drops is gone from that session permanently. Anything you want to survive compaction has to live outside the transcript, in a file that gets re-read — which is why project knowledge belongs in CLAUDE.md or a rules file rather than in something you explained once in chat.
  - q: Is a bigger CLAUDE.md the answer to Claude Code's memory problem?
    a: Only up to a point, and it stops working exactly when you need it most. CLAUDE.md rides in the context window on every single message, so its size is a fixed tax paid per turn, and it grows monotonically as the project does. The scalable shape is a small bounded index that is always loaded plus detail that loads only when it becomes relevant.
  - q: Where does Claude Code store session transcripts?
    a: Under your config directory, at `~/.claude/projects/<encoded-project-path>/<session-id>.jsonl`, one JSON object per line. The folder name is a lossy encoding of the project's real path, and every transcript line records the real working directory in its `cwd` field, which is the reliable way to map a folder back to a project.
  - q: Can Claude Code learn from mistakes across sessions?
    a: Not on its own — nothing reads yesterday's transcript before today's session. It takes an external step that distils durable lessons (an error and its fix, a decision, a stated preference) out of finished transcripts and re-injects the relevant ones later. claudectl does this after each session and gates low-confidence lessons behind a review screen.
---

## The short answer

Claude Code forgets your project because a session *is* the memory. Everything the model knows lives in one context window, and that window is discarded when the session ends. The transcript written to disk is an append-only log — nothing ever reads it back. The only knowledge that survives into the next session is whatever is loaded at startup: `CLAUDE.md`, path-scoped rules files, and anything a `SessionStart` hook injects. Fixing the amnesia means deliberately moving knowledge out of the conversation and onto those surfaces, on a budget, because everything on them is paid for on every message.

That last clause is the part most people skip, and it is why the naive fix fails.

## What is actually on disk

Every Claude Code session becomes a file:

```
~/.claude/projects/<encoded-project-path>/<session-id>.jsonl
```

One JSON object per line — user messages, assistant messages, tool calls, tool results. On a long-running project these get large; a 2,787-message session is an ordinary size for a week of work, and files past 100 MB exist. The folder name is a lossy encoding of the project path (`D:\Projects\my-app` becomes `D--Projects-my-app`), which is why the reliable way to map a folder back to a real project is not to decode the name but to read the `cwd` field that every transcript line already carries.

The important property of that file is negative: **nothing reads it**. It is a record, not a store. Claude Code does not consult yesterday's transcript before answering today's question, and `/resume` does not change that — it reattaches you to one specific conversation in the current directory, which is a different thing from remembering the project.

So the model's knowledge of your codebase has exactly one lifetime: the session. Two events end it early.

**The context window fills.** `/compact` summarises the conversation to make room. It preserves the thread and drops the detail, which is precisely the material you built up over two hours of exploration. After a compact you are working with a model that knows the shape of the task and has lost the specifics.

**The session ends.** New session, empty window, and the reasoning you did yesterday exists only as prose in a file nobody reads.

## The three things that survive

| Surface | When it loads | What it costs |
|---|---|---|
| `CLAUDE.md` (project, user, `.claude/`, local) | Every message | Its full size, per turn, forever |
| `.claude/rules/*.md` with a `globs:` header | Only when Claude touches a matching path | Nothing until then |
| `SessionStart` / `UserPromptSubmit` hook output | Session start, or per prompt | Whatever the hook injects |

That table is the whole design space. Anything you want Claude to know next Tuesday has to arrive through one of those three doors.

## Why "just write a bigger CLAUDE.md" stops working

`CLAUDE.md` is read on every turn. Not every session — every *message*. A 4,000-token file is 4,000 tokens on turn one and on turn two hundred. That is fine at 40 lines and stable conventions; a hand-written `CLAUDE.md` is genuinely the right tool for a small project, and if yours is short and stays short you do not need anything else.

It degrades with size, and it degrades in a specific way: the usual failure is not that the file is wrong, it is that the file is too big to justify and too tedious to prune. So it keeps growing, the per-turn tax keeps rising, and eventually you are paying for a paragraph about a module you deleted in March. The comparison is honest in both directions — [claudectl's own compare page](https://docs.claudectl.space/compare/) says plainly that a 40-line `CLAUDE.md` needs no replacement.

The scalable shape is different: a small always-on index, plus detail that costs nothing until it becomes relevant.

## Knowledge that lives outside the transcript

claudectl's answer is a semantic memory graph stored per project at `.claudectl/memory/graph.json`. Modules and repos are summarised (incrementally, keyed by file hash, so unchanged code is never re-analysed) and merged with the **real** dependency graph extracted from the source — cross-module edges and an importance rank, not a guess from filenames.

Two properties matter more than the extraction:

**It is bounded.** Duplicate entities merge across modules, and a global importance cap (`memory_max_entities`, default 500) evicts the least-connected. The always-on cost stays flat while accuracy rises. That is the inversion that makes this work at all: memory gets *leaner and sharper* as the project grows, instead of heavier.

**Nothing shrinks without a way back.** Any entity or lesson can be pinned so the cap can never evict it — pin more than the cap allows and the pins win, the cap gives way. A section of `CLAUDE.md` can be fenced between `<!-- CLAUDECTL:KEEP:START -->` and `<!-- CLAUDECTL:KEEP:END -->`, and AI compression will not even *send* it to the model, so it cannot be reworded or dropped. Pruning names the exact entries it will remove before removing them. Every replacement is snapshotted — twelve versions of `CLAUDE.md` and of the graph, browsable with a diff and restorable, and restoring is itself snapshotted so you can walk back out again.

That graph then reaches a session through the three surfaces above:

| Surface | What Claude sees | Cost |
|---|---|---|
| CLAUDE.md micro-index | Repo one-liners, module names, a recall pointer | ≤250 tokens, every session |
| `.claude/rules/claudectl-mem-*.md` | Per-module entities and relations, `globs:`-scoped | **0 until Claude touches those files** |
| `UserPromptSubmit` hook (opt-in) | The subgraph relevant to *this prompt* | ≤600 tokens/prompt, under a second, local |

The token arithmetic behind that split is a separate topic — see [how to cut Claude Code token costs](/blog/cut-claude-code-token-costs) for the audit and the numbers.

## Retrieval, and being honest about it

The per-prompt surface only works if retrieval is good. claudectl's recall engine runs four local rankers — BM25 over entity names and summaries, path and module match, dependency rank, and a confidence-weighted lesson signal — combined with Reciprocal Rank Fusion, which uses each ranker's *position* rather than its score, so nothing needs calibrating. No embeddings, deterministic, under half a second on 500 entities. You can call it yourself:

```
claudectl recall "the memory graph consolidation cap"
```

Claude can call it too, mid-session, through Bash — which is the cheapest possible form of on-demand memory, because it costs nothing until something asks.

Two lessons from building it are worth stealing regardless of the tool you use. First, a lexical ranker needs a stopword floor: without one, the query `the` returned 33 entities on this repo's own graph, and the prompt hook cheerfully injected memory into prompts that asked for none. IDF alone never reaches zero. Second, a budget should *fit* rather than truncate — one verbose top hit should not discard every smaller fact behind it.

And the honest limit, stated in [the memory docs](https://docs.claudectl.space/memory/) rather than hidden: this is lexical retrieval. A query that shares no vocabulary with the stored summary will miss, and no weight tuning changes that.

## Facts that stop being true

A memory that only accumulates is a memory that goes stale. When you migrate Flask to FastAPI, "this project uses Flask" is not wrong-forever, it is wrong-from-a-date. claudectl invalidates superseded facts with a timestamp instead of deleting them: the old fact is kept as history and never injected again. The graph tracks what is true *now* and what changed, which is a materially different data model from a pile of notes.

Alongside that, entities recalled often gain weight and survive consolidation; knowledge nobody touches fades, access-based, like a forgetting curve.

## Learning from finished sessions

The transcript nobody reads does contain something worth keeping: the moment you told Claude its fix was wrong, and the fix that actually worked. After each session claudectl distils durable **lessons** — error→fix pairs, decisions, preferences — from the transcript. High-confidence ones auto-approve; the rest wait in a review screen. Approved lessons boost recall and decay if never used again.

The load-bearing detail is that this runs at `SessionEnd`, not `Stop`. `Stop` fires on every turn, and binding a whole-transcript scan to it means re-streaming a growing file dozens of times per session for a heuristic that only needs to run once.

## What to actually do

1. **Install it and build the graph.** `pipx install claudectl`, open the project, press `m` for the memory hub, build. Nothing on disk moves.
2. **Turn on the rules surface.** Per-module knowledge in `.claude/rules/` costs zero until Claude opens a matching file. This is the single highest-value change.
3. **Add the recall hook only if you want per-prompt injection.** It is opt-in, budgeted at ~600 tokens, and runs locally.
4. **Turn on recent-work memory** for a token-free one-line summary per session, injected as a compact digest on the next `SessionStart` — so a new session knows what the last few did.
5. **After a compact, hand off instead of re-explaining.** **Hand off** on the session row (or `⇧K` in the terminal UI) starts a *new* session seeded with the previous transcript, written to `.claudectl/injected-context.md` and passed as a pointer, not a paste.

## Where this does not help

Memory extraction is Claude calls — routed to a cheap model and run rarely, but not free. The saving is on the per-message context you stop paying for, and if your project context is already 40 lines there is nothing to save. Retrieval is lexical, so an oddly-worded prompt will miss. And none of this makes Claude Code remember on its own; it makes the next session start informed, which is the achievable version of the same thing.

Reference detail lives at [docs.claudectl.space/memory](https://docs.claudectl.space/memory/).
