---
title: "How to cut Claude Code token costs: the 250-token index pattern"
description: Context files ride along on every message, so their size is a permanent per-turn tax. How to replace a 4,000-token CLAUDE.md with a bounded index.
date: 2026-09-01
tags: [claude-code, tokens, cost, context]
author: Babar Muhammad Anas
faq:
  - q: What actually uses the most tokens in Claude Code?
    a: Not the conversation — the context that is re-sent with every message. CLAUDE.md, the global `~/.claude/CLAUDE.md`, any always-loaded rules files, a system-prompt file and every connected MCP server's tool schemas are all part of each turn's input. A 4,000-token context block costs 4,000 tokens on turn one and again on turn two hundred.
  - q: How do I see how many tokens my Claude Code project loads per turn?
    a: Run claudectl's context weight audit (`⇧W` in the sessions menu, or the Audit tab in the GUI). It estimates the always-on cost of CLAUDE.md broken into its blocks, the global CLAUDE.md, each rules file (marked lazy when glob-scoped), the system prompt, SessionStart hook injections and MCP schemas, with a running total and warnings for the usual offenders.
  - q: Do .claude/rules files cost tokens if Claude never opens those files?
    a: No, provided the rule file carries a `globs:` header scoping it to specific paths. A glob-scoped rule loads only when Claude touches a matching file, so several kilobytes of per-module detail can sit on disk at zero per-turn cost. A rules file with no globs is always-on and should be counted alongside CLAUDE.md.
  - q: Does a large CLAUDE.md slow Claude Code down or just cost more?
    a: Both, indirectly. Every token of always-on context is input on every request, so it adds latency and cost per turn, and it consumes context-window space that would otherwise hold the actual work — which brings `/compact` forward and loses conversation detail sooner.
  - q: Can I stop Claude Code from reading node_modules and lockfiles into context?
    a: Yes, with `permissions.deny` rules in the project's `.claude/settings.json`. claudectl's audit can scan the project and generate them (`node_modules/**`, `dist/**`, lockfiles and similar), merging into the existing settings rather than replacing them, so one stray read cannot pull thousands of tokens of generated content into the window.
---

## The short answer

The expensive part of Claude Code is not the conversation, it is the context that gets re-sent with every message. `CLAUDE.md`, the global `~/.claude/CLAUDE.md`, always-loaded rules, a system-prompt file and every MCP server's tool schemas are all input on *each turn*, so their size is a fixed per-turn tax that grows monotonically as you add to them. The fix is structural, not editorial: keep a bounded always-on index of about 250 tokens, push per-module detail into path-scoped rules that load only when Claude touches a matching file, and inject task-specific knowledge per prompt inside a budget. Measure it first — most projects have never once looked at the number.

## Measure before you cut

Nobody's estimate of their own `CLAUDE.md` is accurate. Run the context weight audit:

```
claudectl          # open the project, then ⇧W in the sessions menu
```

or the **Audit** tab in the GUI, which does the same thing project-scoped and account-scoped together. It itemises everything auto-loaded per turn:

- `CLAUDE.md`, broken into its blocks — your prose, the AUTOGEN block, the session-topics log, the memory digest — each with its own token count
- the global `~/.claude/CLAUDE.md`, which loads in *every* project on that account
- `.claude/rules/*`, each marked **lazy** when glob-scoped
- `system-prompt.txt`
- `SessionStart` hook injections
- MCP server tool schemas

with a running always-on total and inline warnings: a `CLAUDE.md` over 200 lines, an unbounded session-topics block, a global `CLAUDE.md` that rides in every project.

The two findings that show up most often are boring and large. First, a session-topics log that has been appending since January. Second, MCP servers whose tool schemas are in context on every turn whether or not you use them this session.

## The pattern: one bounded index, everything else lazy

The failure mode of a hand-maintained context file is that it is asked to be two things at once — a stable set of instructions *and* an encyclopaedia of the codebase. Those have opposite cost profiles. Instructions are short and always relevant. Codebase knowledge is long and relevant maybe five percent of the time.

Split them:

| Tier | Content | Load | Budget |
|---|---|---|---|
| Always-on index | Repo one-liners, module names, a pointer to on-demand recall | Every message | **≤250 tokens** |
| Path-scoped rules | Per-module entities, relations, conventions | Only when Claude opens a matching path | 0 until then |
| Per-prompt injection | The subgraph this prompt actually needs | Once per prompt, opt-in hook | ≤600 tokens |

The number that matters is not 250, it is *bounded*. A 250-token index that grows to 900 next quarter has failed. Keeping it flat takes two mechanisms: consolidation, which merges duplicate entities across modules, and an importance cap (`memory_max_entities`, default 500) that evicts the least-connected entity when the graph exceeds it. Per-repo rollup summaries — built locally, no model call — give the index its one-line-per-repo overview without listing everything underneath.

The net effect is that the always-on cost stops tracking codebase size. A ten-module project and a sixty-module project pay the same per-turn tax; the sixty-module project just has more that is available lazily.

## Making the lazy tier actually lazy

A rules file only costs nothing if Claude Code knows when it is irrelevant. That is what the `globs:` header does — the file is loaded when a matching path is touched and skipped otherwise. claudectl generates one per module:

```
.claude/rules/claudectl-mem-<project>-<module>.md
```

each containing that module's entities, their types, one-line summaries and the relations between them. Several kilobytes across the set, zero per-turn cost.

The audit marks these **lazy** for exactly this reason, and it is worth checking your own hand-written rules files for a missing `globs:` — a rule without one is always-on, and it will be sitting in the always-on column where you did not expect it.

The GUI's Memory tab states the trade-off in one sentence it took a while to get right: *~N tok read on every turn, ~M tok across the rule files that cost nothing until Claude opens a matching path.* That is the whole design in a line.

## Prune the parts that grow forever

Two blocks in a generated `CLAUDE.md` are unbounded by nature.

**Session topics.** Every session adds a line. Nothing removes one. Capped now to the most recent N (`claude_md_sessions_cap`, default 10); `p` in the audit rebuilds the block in place.

**The autogen commit list.** Configurable with `claude_md_commits`.

Prune touches only those blocks. Your manual prose and the memory digest are untouched — which is the whole reason the file is structured as blocks rather than as text. In the GUI's CLAUDE.md tab each block shows its own token cost and its own regenerate button, so you can rebuild the session log without regenerating anything you wrote.

## Compress the prose you wrote by hand

`⇧C` rewrites the hand-written portion into a lean lookup-table style, targeting under 500 tokens. It shows a before→after token count and a git-style diff to approve, keeps a `CLAUDE.md.bak`, and preserves the machine-maintained blocks verbatim.

If part of your prose must survive verbatim — a legal note, a hard-won gotcha with exact wording — fence it:

```markdown
<!-- CLAUDECTL:KEEP:START -->
Never regenerate the migration order. See docs/migrations.md.
<!-- CLAUDECTL:KEEP:END -->
```

A fenced section is not *sent to the model at all* during compression, so it cannot be reworded, shortened or dropped. That is a stronger guarantee than "the model was told to preserve it".

## Stop stray reads from blowing the window

One `cat package-lock.json` can cost more than a week of `CLAUDE.md`. The audit's `d` action scans the project and writes `permissions.deny` rules into `.claude/settings.json` — `node_modules/**`, `dist/**`, lockfiles and the rest — merging into your existing settings rather than clobbering them.

Two hooks attack the same class of waste from the other end:

- **`concise-output`** — a `SessionStart` rule that suppresses narration and re-printed code.
- **`filter-test-output`** — rewrites `pytest` / `npm test` / `go test` commands to pipe through a failures-only filter *before* the output reaches context. A green test run that prints 400 lines of dots is 400 lines of input tokens on the next turn.

Both are one-key installs from the hooks manager. Note that a `SessionStart` hook that injects text is itself a per-turn cost, which is why the audit counts hook injections in the same table as everything else.

## Spend less per turn on the model itself

Two levers, separate from context size.

**A think cap and a cheap subagent model.** The launch-options screen exposes `MAX_THINKING_TOKENS` (Think cap) and `CLAUDE_CODE_SUBAGENT_MODEL` (Subagents). The `e` key applies an economy preset in one keystroke: Sonnet, an 8k thinking cap, Haiku subagents. Subagent model choice is underrated — a fan-out of six searches on the same model as your main session is six times the rate you meant to spend.

**A cheap model for the tool's own calls.** claudectl's internal Claude calls — memory extraction, lesson distillation, CLAUDE.md and hook and skill generation — default to Haiku (`extract_model` in Settings → Economy model). Your actual coding sessions keep whatever model you picked. Worth checking in any tool that makes model calls on your behalf: the default is often the same expensive model you use for the work.

Beyond cheap there is free — plan on an accurate model and execute on a free one, covered in [plan on an expensive model, execute on a free one](/blog/plan-expensive-execute-cheap).

## Compact instructions

Auto-compaction is going to happen on a long session. What it keeps is steerable: a `# Compact instructions` section in `CLAUDE.md` tells Claude Code what matters when it summarises. Scaffolded and AI-generated files include one; the audit offers to add it (`i`) when it is missing. It costs a handful of tokens per turn and changes what you still have after the compact, which is a good trade.

## An order of operations

1. Run `⇧W`. Write down the always-on total.
2. Cap or prune the session-topics block. Usually the largest single win, and it costs nothing.
3. Move per-module knowledge to `.claude/rules/` with `globs:`. Check the audit re-marks them lazy.
4. Compress the hand-written prose (`⇧C`), fencing anything that must survive word for word.
5. Generate deny rules (`d`) so one stray read cannot undo the rest.
6. Set the economy preset for routine work; keep the strong model for design.
7. Re-run `⇧W`. The number should be flat from here as the project grows, not rising.

The point of the exercise is not a smaller file. It is that the always-on cost stops being a function of how big your codebase got.

Reference detail: [docs.claudectl.space/usage](https://docs.claudectl.space/usage/).
