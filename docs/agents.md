---
description: >-
  The agent library, per-project subagent selection, adaptive suggestions, AI-generated
  agents, and the skills manager — everything claudectl does with Claude Code subagents and
  SKILL.md files.
---

# Agents & skills

## Agents (subagents)

- **Agent library** — a category-organized store at `~/.claude/claudectl-agents/<category>/` (not auto-loaded by Claude, so sessions stay lean). Roll your own or bulk-install the [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) catalog (150+ agents across 10 categories) — see [Installing the agent library](agent-library.md).
- **Per-project selection** (`g` in the sessions menu) — pick agents from a category checklist (optional, default none). The chosen agents are **copied into `<project>/.claude/agents/`** where Claude auto-discovers them, so they apply to every launch of that project and the selection auto-restores next time. claudectl only manages the files it placed (tracked in `.claudectl-managed.json`) — your own project agents are never touched.
- **Scaffold** — create an agent into a chosen or new category: pick tools (multi-select) and model, edit the body
- **AI-generated** — Claude analyzes the project and authors a focused subagent (role, when-to-use, tool subset, system prompt); you review before it's written
- **Lead agent** — also set a single `--agent` (from `~/.claude/agents/`) in launch options
- **Why copy, not `--agents`** — inline `--agents` JSON rides the command line (Windows ~32KB cap); a handful of real, multi-KB agents overruns it (`WinError 206`). Copying into `.claude/agents/` has no size limit and matches how Claude Code natively loads project subagents.

## Adaptive agent selection (`g`)

The agents screen opens with a **"Suggested for this project"** section — library agents
ranked against the project's languages (from the [dependency graph](architecture.md)), memory
entities, and name. Local scoring, instant, free. Setting `agents_auto: 'auto'` applies
suggestions automatically on first open (your manual picks are never touched).

## Getting the agents you install actually used

Copying agent files into a project makes them **available**. It does not make them
**used**: Claude Code decides to delegate by matching the task against each agent's
`description`, and library descriptions read as catalogue entries, so a project can carry
ten agents that never fire. Two things claudectl does about it:

- **A delegation table in `CLAUDE.md`.** Applying a selection writes a
  `CLAUDECTL:AGENTS` sentinel block naming the installed agents and the trigger from each
  one's own frontmatter. `CLAUDE.md` is read on **every** turn, which makes it the one
  place that changes the outcome. The block is regenerated from what is on disk and
  disappears with the last agent, so it can never name something that is not there.
- **Usage, from Claude Code's own record.** The picker marks each agent with when it was
  last delegated to (`agentLastUsed` in `.claude.json`). An agent installed months ago and
  never used is the one to remove — and the honest answer to "is any of this working?".

- **Sharpen descriptions** (GUI → *Agents* → *Sharpen descriptions*). Rewrites the
  `description` of every agent into trigger form — *Use PROACTIVELY when …, do not use
  for …* — and you approve the whole before/after list before anything is written. Only
  that field changes; bodies and every other frontmatter field are re-emitted
  byte-for-byte, because the description is the only thing the router reads.

    It is machine-wide, not per project: every account's user-level agents, every
    project's `.claude/agents`, and the claudectl library, so sharpening also improves
    every *future* install. Agents that share a name and description are one question and
    many writes — the same agent in twelve projects is not twelve chances to get twelve
    different answers. Each touched project's CLAUDE.md routing table and nudge index are
    refreshed once afterwards.
- **A prompt-match hook** (`suggest-subagent`, in the Hooks manager). On every prompt it
  matches your words against the installed agents' triggers and, when something clearly
  fits, adds one line naming it. No model call, and silence when nothing matches — a hook
  that fires every turn has to be quiet by default. It reads one small index that the agent
  sync writes, never the agent files.

Three habits that matter more than any setting: keep the set small (the picker warns past
ten, and a long list dilutes every description), prefer the **Suggested** section, which
ranks against what the project actually is, and prefer agents whose description names a
trigger ("use when the task involves X") over ones that describe a job title.

## Skills

claudectl shows the scopes **Claude Code itself loads**, in the order it resolves them:

| Scope | Where | Applies |
|---|---|---|
| Personal | `<account>/skills/<name>/SKILL.md` | every project on that account |
| Project | `<project>/.claude/skills/<name>/SKILL.md` | that project |
| Plugin | shipped by an installed plugin, as `/plugin:skill` | wherever the plugin is enabled |
| Built-in | inside Claude Code, not on disk | everywhere |

A personal skill wins over a project skill of the same name, and a shadowed project skill
is labelled as such rather than listed as if it ran. **The command is the folder name** —
for a personal or project skill the frontmatter `name` is only a display label — so that is
what each row shows.

**Personal means you, not one login.** Installing into the personal scope writes into every
configured account, and deleting removes it from all of them; `sync-accounts` levels skills
the same way it levels hooks and agents.

### Two usage signals, never one number

`skillUsage` — the counter Claude Code keeps — counts exactly one thing: the times you
**typed** `/name`. A skill Claude loads on its own, and a plugin that works through a
`SessionStart` hook, never touch it. That is why caveman can read as *"used twice, 56 days
ago"* while shaping every session you run. So the page shows two things:

| Signal | What it means | Where it comes from |
|---|---|---|
| `typed 12× · 2d` | you invoked it by name | `skillUsage`, merged across every account |
| `in 27/30 sessions` | something by that name actually ran | your transcripts' hook records |

Two flags explain the rest: **manual only** (`disable-model-invocation: true` — Claude may
never load it on its own) and **thin description** (Claude picks a skill by matching your
task against its description, so a one-word one can only be found by name).

Install from the bundled starters (see [Credits](credits.md)), write one by hand, have
Claude author one, or clone a skill+agent bundle from GitHub — every third-party bundle is
statically risk-scanned and shown to you before anything is written. TUI: **⚙ Skills**;
GUI: the **Skills** page.

!!! note "Upgrading from 1.7 or earlier"
    claudectl used to keep "your library" in `~/.claude/claudectl-skills`, which **no
    Claude Code reads** — saving a skill there looked like installing it and did nothing.
    That folder is copied into every account's `skills/` directory once, on the next start,
    and left in place as a backup.
