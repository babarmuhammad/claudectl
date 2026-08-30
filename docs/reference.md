---
description: >-
  Where claudectl stores things — per-project files, the workspace provenance manifest,
  CLAUDE.md auto-generation, the global CLAUDE.md, session path encoding and the repository
  file layout.
---

# Files, layout & encoding

## Per-project files

Each project gets a folder at `~/.claude/projects/<encoded-name>/`. claudectl reads and
writes several files there:

| File | Purpose |
|------|---------|
| `<session-id>.jsonl` | Claude Code session transcript (managed by Claude Code) |
| `<session-id>.name` | Custom display name you set with r |
| `extra-paths.txt` | Additional PATH directories added when launching Claude |
| `add-dirs.txt` | Directories passed via `--add-dir` on every launch |
| `system-prompt.txt` | System prompt injected via `--system-prompt-file` on every launch |
| `tags.json` | Per-session tags (`sid → [tags]`) |
| `session-agents.json` | Selected agent refs, keyed by `__project__` (project-level picks) |
| `archived/` | Archived sessions (restorable from the A view) |

In the project's **working directory** (not the encoded folder), claudectl also maintains:

| File | Purpose |
|------|---------|
| `.claude/agents/*.md` | Selected library agents, copied here so Claude auto-discovers them |
| `.claude/agents/.claudectl-managed.json` | Filenames claudectl placed (so it never removes your own agents) |
| `.claudectl/workspace-manifest.json` | Provenance & freshness manifest (repo HEAD, hashes, sessions, MCP, timestamps) |
| `.claudectl/memory/graph.json` | Claude-extracted semantic memory (entities, relations, per-repo/module summaries) |
| `.claudectl/connections-cache.json` | Cached architecture graph (rebuilt when the file signature changes) |
| `.claudectl/connections-graph.html` | The rendered interactive architecture graph (opened in the browser) |
| `.claudectl/snapshots/` | Previous versions of generated files (for the `w` change diffs) |

The agent library lives at `~/.claude/claudectl-agents/<category>/*.md` (account-wide, not
auto-loaded); selecting agents for a project copies them into that project's
`.claude/agents/`. A single lead agent can also come from `~/.claude/agents/`. Hooks and MCP
servers are stored in `settings.json` / managed via `claude mcp`.

claudectl's own settings live at `~/.claude/claudectl.json` — always read from `~/.claude`,
independent of the config dir you are using.

## Workspace status

claudectl tracks the **provenance and freshness** of the context it generates. After
scaffold, AI-analyze, or launch, it writes `<project>/.claudectl/workspace-manifest.json`
(falling back to the encoded `~/.claude/projects/<encoded>/.claudectl/` folder if the working
dir is read-only). The manifest is schema-versioned and forward-compatible — old files load,
unknown keys survive round-trips.

It records where generated context came from: repo HEAD, source-file hashes
(CLAUDE.md/README/configs), sessions analyzed (count + range), CLAUDE.md files, MCP server
snapshots + tool counts, and last-run timestamps for scaffold / AI-analyze / launch. Updated
automatically after those operations (best-effort — never blocks them).

View it from inside a repo:

```
$ claudectl workspace status
  Workspace Status
  ────────────────
  Repo HEAD         5f39fcb  (main)
  Sessions analyzed 20
  MCP servers       3
  CLAUDE.md status  🟢 Fresh
  MCP docs status   🟢 Fresh
  Repo changed      No
  Safe to launch    Yes

  Workspace freshness score: 96%  ▕███████████████████░▏
```

…or press `w` in the sessions menu for the same view as a TUI screen (`r` refreshes, ESC
exits). Indicators: 🟢 Fresh · 🟡 Stale · 🔴 Invalid. A component goes **stale** when the
repo HEAD moved, README/source hashes changed, or new sessions accrued since the memory was
generated; **invalid** means a missing-after-generation CLAUDE.md or a corrupt manifest.
`safe_to_launch` is false only when an invalid check is present. The freshness score is the
weighted fraction of applicable checks that are fresh. Viewing status is **read-only** — it
never rewrites the manifest.

**Change diffs** — when AI-regenerating CLAUDE.md (`a`) or a system prompt (`s`), the
approval step shows a **git-style colored diff** (old → new) so you decide *before* writing
(`f` toggles to the full proposed text; ENTER approve, ESC reject). The previous version is
snapshotted under `.claudectl/snapshots/`, so the workspace screen (`w`) lists recent changes
with `+/−` counts and re-opens the last diff on `c` (CLAUDE.md) / `s` (system prompt).

## CLAUDE.md auto-generation

**`c` — Scaffold (fast, mechanical)** builds CLAUDE.md from:

- Git repos found up to 2 levels deep in the project and any linked extra paths
- Last 7 commits from each repo (`git log --oneline -7`)
- First 15 lines of each repo's README
- All session topics (accumulated, never discarded)

On an existing file, only the `<!-- AUTOGEN:START -->…<!-- AUTOGEN:END -->` and
`<!-- SESSIONS:START -->…<!-- SESSIONS:END -->` blocks are replaced. Everything outside those
blocks is preserved exactly.

**`a` — AI analyze (slower, comprehensive)** runs `claude.exe -p` with a rich prompt
containing the full directory tree, git history, READMEs, extra paths, and session history.
Claude writes the entire CLAUDE.md. You review it in a pager and approve or reject before any
file is written.

On an existing file, the current content is passed as ground truth with instructions to
update only facts that have clearly changed. After generation the
`<!-- AUTOGEN:START/END -->` and `<!-- SESSIONS:START/END -->` blocks are injected
mechanically, and `<!-- AI:ANALYZED -->` is inserted on line 2 so future runs enter update
mode rather than fresh mode.

## Global CLAUDE.md

`~/.claude/CLAUDE.md` is loaded by Claude Code in every session across all projects.
claudectl uses it to store MCP tool documentation. Each MCP server gets its own
sentinel-delimited section:

```
<!-- MCP:Notion:START -->
## MCP: Notion
… tool listing …
<!-- MCP:Notion:END -->
```

Re-running the analysis for the same server updates only that section; other content is
untouched. Access via: main screen → **⚙ Global CLAUDE.md / MCP Analysis**.

## Session encoding

Claude Code encodes project paths as folder names under `~/.claude/projects/` by replacing
path separators with `--` and certain special characters with `-`. For example:

```
D:\Projects\my-app  →  D--Projects-my-app
```

The encoding is lossy, so `find_actual_path()` in `paths.py` does not try to decode it. It
reads the real path out of the `cwd` field that every transcript line already records, and
only falls back to walking the filesystem and matching encoded components (handling `_`,
`+`, `-`, `#` in directory names) when a project folder has no transcript to read. That
ordering is what makes UNC paths work: `\\server\share\Project` encodes to
`--server-share-Project`, which no amount of splitting on `--` can turn back into a drive
letter.

## File layout

```
.\claudectl\
├── claude-sessions.py      # launcher stub: applies theme, --launch, crash handler
├── Open Repo cmd.bat       # bat launcher (runs TUI, then py --launch)
├── pyproject.toml
├── README.md
├── tools\                  # dev utilities: GUI smoke/screenshot audits, graph renders, icons
├── tests\                  # pytest suite (Windows-only, no network, no real claude.exe)
└── claude_sessions\        # package
    │
    │  # entry points
    ├── main.py             # run() — subcommand dispatch, project discovery, launch flow
    ├── cli.py              # console-script target; dispatches statusline before importing main
    ├── __main__.py         # `python -m claude_sessions`; same early statusline dispatch
    │
    │  # core
    ├── config.py           # constants, paths, settings, write_atomic, theme application
    ├── paths.py            # encode_component, find_actual_path, resolve_dir
    ├── sessions.py         # session parsing + persistence helpers
    ├── render.py           # frame-diff renderer, layout + hint helpers
    ├── themes.py           # PALETTES / SKINS / WORLDS — single source of truth for colour
    │
    │  # TUI screens
    ├── ui.py               # menu, pager, multiselect, confirm, launch options, settings
    ├── session_menu.py     # per-project sessions menu
    ├── search.py           # cross-project session search
    ├── transcript.py       # transcript viewer + markdown export
    ├── stats.py            # usage stats dashboard
    ├── usage.py            # plan usage limit bars (OAuth poll)
    ├── brief.py            # "since last session" digest
    ├── checkpoints.py      # read-only view of Claude Code's file-history store
    │
    │  # Claude Code integration
    ├── mcp.py              # MCP manager + background status poll
    ├── agents.py           # agent library, per-project selection, scaffold/AI
    ├── skills.py           # skills manager + bundled starter templates
    ├── skillscan.py        # static risk scan of a skill before installing it
    ├── hooks.py            # hooks template / toggle / remove
    ├── plugins.py          # plugin marketplaces + installs (shells out to `claude`)
    ├── outputstyles.py     # output-style browse / save / select
    ├── statusline.py       # `claudectl statusline` — renders the Claude Code status line
    ├── accounts.py         # multiple CLAUDE_CONFIG_DIR accounts
    ├── denygen.py          # generated permissions.deny rules for heavy paths
    ├── health.py           # project health checks + auto-fixes
    ├── *_hook.py           # the hook scripts themselves (guard, recall, worklog, …)
    │
    │  # memory & context
    ├── memory.py           # Claude-powered semantic memory (ECL + ask)
    ├── memhub.py           # cross-project memory index
    ├── memrules.py         # per-module .claude/rules generation
    ├── lessons.py          # durable lessons distilled from transcripts
    ├── recall.py           # `claudectl recall "<topic>"` — task-relevant subgraph
    ├── worklog.py          # recent-work ring buffer per project
    ├── conventions.py      # inferred repo conventions
    ├── context_inject.py   # cross-session context hand-off
    ├── ctxaudit.py         # context weight audit
    ├── claude_md.py        # scaffold + AI CLAUDE.md, autogen/sessions blocks
    ├── system_prompt.py    # edit / AI-generate the per-project system prompt
    │
    │  # git & repos
    ├── repos.py            # repo discovery, cached state, _git (the one git door)
    ├── worktrees.py        # linked-worktree board
    ├── workspace.py        # provenance manifest + freshness status
    ├── review.py           # `claudectl review` — diff review
    ├── diffview.py         # git-style diffs + the approval gate for generated files
    ├── connections.py      # project architecture graph (standalone HTML)
    │
    │  # model routing
    ├── plan_execute.py     # Plan→Execute: plan with one model, execute with another
    ├── omniroute.py        # provider seam: prepare_launch + OmniRoute catalog/health
    ├── failover.py         # local proxy: retry a dead model instead of hanging
    │
    │  # GUI
    ├── gui.py              # loopback HTTP server, _guard(), launch endpoint
    ├── gui_api.py          # GUI job layer — TUI flows headless + diff-approval gates
    ├── gui_html.py         # page assembly + the /vendor/ allowlist
    ├── gui_qt.py           # optional PyQt6 native window shell
    ├── web\                # the SPA: app.js, app.css, stage.js, motion.js, instruments.js
    └── skills_templates\   # bundled starter SKILL.md files
```

The HTTP routes the GUI is built on are catalogued in the [API reference](api.md),
generated from the route tables themselves.
