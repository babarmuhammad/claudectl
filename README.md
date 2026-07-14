<h1 align="center">claudectl</h1>

<p align="center">
  <b>The workspace layer for Claude Code.</b><br>
  Persistent project memory, an interactive architecture graph, MCP awareness, and per-project launch control — in a fast terminal UI.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-0078D6">
  <img alt="Dependencies" src="https://img.shields.io/badge/runtime%20deps-zero%20(stdlib)-brightgreen">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Claude Code" src="https://img.shields.io/badge/for-Claude%20Code-8A5CF6">
</p>

<p align="center">
  <img alt="Architecture graph" src="docs/graph-real.gif" width="820">
  <br><sub>The live interactive architecture graph — rotating dodecahedron nodes, per-project bubbles, flowing dependency links (captured from the real HTML view).</sub>
</p>

---

Claude Code treats your work as a stream of chats. **claudectl treats each project as a persistent workspace** — sessions stay browsable and searchable, project context lives in maintained CLAUDE.md files, a Claude-built **semantic memory** layer feeds the agent real project knowledge, an interactive **architecture graph** shows how the codebase connects, MCP servers are visible at a glance, and every launch is configured per project. Switching projects stops feeling like losing the agent's memory.

**Why claudectl**

- 🧠 **Intelligent memory, not a memory dump** — nobody else does task-scoped, token-budgeted injection at the launcher: a micro-index always on (≤250 tok), per-module detail loaded only when Claude touches those files, and an optional per-prompt hook that injects just the subgraph relevant to what you asked.
- 📚 **It learns from every session** — durable lessons (fixes, decisions, preferences) distilled from transcripts, human-reviewed, injected when relevant, decayed when stale.
- 🕸️ **See your architecture** — an animated, expandable dependency graph (Python · C/C++ · C# · JS/TS) that opens at the project level and drills down to single files.
- 🩺 **Auto-solves common Claude Code pain** — pre-launch health checks, context-loss insurance after `/compact`, permission-fatigue killer, token-burn advisor, daily usage tracking.
- 🤖 **Adaptive agents** — the right subagents suggested (or auto-applied) per project from local signals.
- 📦 **Workspace, not chats** — browse, search, tag, fork, resume, and archive every Claude Code session across every project.
- ⚡ **Zero runtime dependencies** — pure Python standard library, Windows-native, uses your existing Claude Code auth (no extra API key).

### How claudectl saves tokens

Without claudectl, a big project either starves the agent (no context) or floods it (a huge CLAUDE.md loaded every message). claudectl spends the *minimum* tokens for the *maximum* relevant context:

- **Flat always-on cost** — the CLAUDE.md block is a ≤250-token index, not a full dump; it does **not** grow as the codebase grows (consolidation + rollups keep it bounded).
- **On-demand detail** — per-module knowledge lives in path-scoped `.claude/rules/` (loads only when Claude touches those files) and in `claudectl recall`, so nothing is paid for until it's relevant.
- **Task-scoped injection** — the optional prompt hook injects only the subgraph your prompt actually needs (budgeted, default ≤600 tok), instead of everything.
- **No stale weight** — superseded facts are invalidated, not carried; dead entities are evicted; only current, useful knowledge is ever sent.
- **Cheaper model for the grunt work** — Plan→Execute runs the expensive model once for the plan and a cheap one for execution; the token-burn advisor nudges you off Opus for routine work.

---

## Contents

- [Features](#features)
- [Install](#install)
  - [Requirements](#requirements)
  - [Setup](#setup)
  - [Installing the agent library](#installing-the-agent-library)
- [Usage](#usage)
  - [Main screen](#main-screen)
  - [Built-in screens](#built-in-screens)
  - [Key bindings](#key-bindings)
- [Reference](#reference)
  - [Per-project files](#per-project-files)
  - [Workspace status](#workspace-status)
  - [CLAUDE.md auto-generation](#claudemd-auto-generation)
  - [Global CLAUDE.md](#global-claudemd)
  - [Session encoding](#session-encoding)
  - [File layout](#file-layout)
- [Troubleshooting](#troubleshooting)

---

## Features

### Session management
- **Session browser** — every Claude Code project and session, sorted by recency
- **Quick-resume** — ★/☆ shortcuts on the main screen jump straight back into recent sessions across all projects
- **Search** — type to filter sessions live; **🔍 Search all sessions** finds and resumes any session across every project
- **Transcript viewer & export** — read any session in a pager (`v`) with full-text search inside the conversation (`/`, `n`/`p` to jump between matches) and a message-position counter; export to markdown (`e`)
- **Session info** — per-session tokens, est. cost, models, git branch, duration (`i`)
- **Archive** — move sessions to a restorable `archived/` folder instead of deleting (`d`, toggle view with `A`)
- **Rename / Fork / Continue** — rename (`r`), fork (`f`), or continue the latest session (`claude -c`)
- **Tags** — tag sessions (`t`); tags show inline and are searchable
- **Changed files** — list the files a session edited/created, derived from its tool calls (`F`)

### MCP servers
- **Full management** — add, remove, and inspect MCP servers via `claude mcp` (scopes local/user/project, transports stdio/http/sse, env vars and headers)
- **Status footer** — connected servers shown live on the main screen
- **Tool documentation** — analyze any server's tools and write the docs into the global `~/.claude/CLAUDE.md`

### Agents (subagents)
- **Agent library** — a category-organized store at `~/.claude/claudectl-agents/<category>/` (not auto-loaded by Claude, so sessions stay lean). Roll your own or bulk-install the [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) catalog (154 agents across 10 categories) — see [Installing the agent library](#installing-the-agent-library).
- **Per-project selection** (`g` in the sessions menu) — pick agents from a category checklist (optional, default none). The chosen agents are **copied into `<project>/.claude/agents/`** where Claude auto-discovers them, so they apply to every launch of that project and the selection auto-restores next time. claudectl only manages the files it placed (tracked in `.claudectl-managed.json`) — your own project agents are never touched.
- **Scaffold** — create an agent into a chosen or new category: pick tools (multi-select) and model, edit the body
- **AI-generated** — Claude analyzes the project and authors a focused subagent (role, when-to-use, tool subset, system prompt); you review before it's written
- **Lead agent** — also set a single `--agent` (from `~/.claude/agents/`) in launch options
- **Why copy, not `--agents`** — inline `--agents` JSON rides the command line (Windows ~32KB cap); a handful of real, multi-KB agents overruns it (`WinError 206`). Copying into `.claude/agents/` has no size limit and matches how Claude Code natively loads project subagents.

### Project memory
- **Scaffold CLAUDE.md** (`c`) — build project context mechanically from git repos, recent commits, READMEs, and prior session topics
- **AI CLAUDE.md generation** (`a`) — Claude deep-analyzes the codebase and writes/updates a comprehensive CLAUDE.md; reviewed before writing
- **System prompts** (`s`) — AI-generate or hand-edit a per-project system prompt injected on every launch
- **Memory map** (`M`) — see which CLAUDE.md files load for a project (user / project / .claude / local) and their `@import`s; open any in your editor

### Architecture graph (`n` → `o`)
An interactive, **whole-project dependency graph** rendered as a self-contained HTML (no CDN), opened in your browser.

- **Expandable hierarchy** — opens at the workspace root + its repos (sized by importance); **click a node to drill in** (repo → module → file) with a smooth opening animation. The complete tree is embedded, so any size is explorable via progressive disclosure; small projects auto-expand fully.
- **Real dependencies, multi-language** — edges come from actual imports: Python `import` (AST) + C/C++ `#include` + C# `using`→namespace + JS/TS `import`/`require`. Edges **lift to the visible level**: collapsed shows repo↔repo bundles, expanded reveals module- and file-level links.
- **Reads as architecture** — each project sits in its **own contained bubble** (never overlaps others), nodes sized by importance (file count + dependency degree), colored per project, animated **rotating dodecahedra** with flowing connection particles on a neural-network-style canvas.
- **Controls** — search (expands the path to matches), filters (dependency / containment / hulls / labels), Fit / Reset / Expand-all / Collapse; zoom-aware labels; hover highlights neighbors. Built graph is **cached** (`.claudectl/connections-cache.json`) so reopening is instant; `r` forces a rebuild.

> The animation at the top of this README is captured from the real HTML view (`docs/graph-real.gif`, regenerate with `py tools/capture_graph_gif.py`). The graph is a self-contained interactive HTML you open in the browser.

### Intelligent project memory (`m`)
The feature that makes claudectl unique: **task-scoped, token-budgeted memory injection at the launcher**. Claude remembers the whole project while paying the fewest possible tokens — three injection surfaces, zero duplication:

| Surface | What Claude sees | Cost |
|---|---|---|
| CLAUDE.md micro-index | repo one-liners + module names + recall pointer | ≤250 tok, every session |
| `.claude/rules/claudectl-mem-*.md` | per-module entities & relations, `globs:`-scoped | **0 until Claude touches those files** |
| `UserPromptSubmit` hook (opt-in) | the subgraph relevant to *your current prompt*, budget-cut | ≤600 tok/prompt, <1s local |

- **Whole-project extraction** — `claude.exe` summarizes every repo and module (incrementally by file hash), merged with the **real dependency graph** (cross-module edges + importance rank) from the connections engine. Stored in `.claudectl/memory/graph.json`.
- **Bounded & self-consolidating** — the graph stays lean *as the project grows*: duplicate entities merge across modules, and a global importance cap (`memory_max_entities`, default 500) evicts the least-connected. So the always-on token cost stays flat while accuracy rises — the memory gets *leaner and sharper* the more you build, not heavier.
- **Temporal facts (Graphiti-style)** — when the code changes and a fact is superseded (you migrated Flask→FastAPI), the old fact is **invalidated with a timestamp, not deleted** — kept as history, never injected. Memory tracks *what's true now* and *what changed*, instead of drifting stale.
- **Reinforcement + rollups** — entities recalled often gain weight and survive consolidation; dead knowledge fades (access-based, like a forgetting curve). Per-repo **rollup summaries** (GraphRAG-style, built locally — no extra Claude call) give an accurate one-line repo overview and cheap global answers. Plus Obsidian-style **unlinked-mention** edges enrich retrieval for free.
- **Recall engine** — local scoring (IDF keyword + path match + dependency rank + graph expansion), no embeddings, deterministic, <0.5s on 500 entities. On-demand CLI: `claudectl recall "<topic>"` — Claude itself can call it mid-session via Bash.
- **Session learning** — after each session claudectl distills durable *lessons* (error→fix pairs, decisions, preferences) from the transcript. High-confidence lessons **auto-approve** (`memory_lessons_autoapprove`); the rest wait in the `⇧L` review screen. Approved lessons boost recall and decay if unused. The project literally gets smarter the more you use it.
- **Cross-project conventions** — preferences/corrections that recur across your repos (or you pin) are promoted to a small block in your user-level `~/.claude/CLAUDE.md`, so a convention learned once ("this machine uses PowerShell 5.1", "prefer pytest") is remembered in *every* project. No competitor spans projects.
- **Auto-refresh** — memory refreshes incrementally on project open (`memory_auto_refresh`, capped so a big rebuild never runs silently). Zero user action. The update runs in a **detached background worker** that survives launching a session, saves after every step (an interruption never loses progress), and shows live progress in the sessions menu — so you can open a chat immediately instead of waiting for the scan to finish.
- **Memory hub** (`m` in the sessions menu) — one screen for everything: status, build, ask, injection preview with live "what would my prompt inject?" probe, lessons, **work suggestions** (`s` — next-steps from lessons + graph + health, local), **since-last-session diff** (`d` — git + session-log), per-surface toggles.
- **Ask the project** — grounded Q&A over the graph, answered by Claude with only the relevant subgraph as context.
- *(Graph memory inspired by [cognee](https://github.com/topoteretes/cognee); retrieval budgeting inspired by [Aider's repo-map](https://aider.chat/docs/repomap.html); both reimplemented from scratch — pure stdlib.)*

### Project health & auto-fixes (`w`)
Launcher-side mitigations for the most common Claude Code problems (2026 field research):

- **Pre-launch health card** — CLAUDE.md over-budget (loads every session!), missing `--add-dir`/PATH entries, non-UTF-8 CLAUDE.md, stale memory, MCP failures, session-window burn ≥70% (suggests cheaper model/effort for routine work).
- **Context-loss insurance** — after every session a 5-line summary (goal + files touched) is appended to `.claudectl/session-log.md`, so the next session can recall what happened even after `/compact` wiped the context. Local, free.
- **Permission fatigue killer** — `P` in the workspace screen scans your history for repeatedly-used Bash commands and proposes `permissions.allow` rules for the project settings.json (diff-previewed, you approve).

### Multiple Claude accounts (⚙ Accounts)
Run two (or more) accounts with almost no friction — claudectl owns the config dir (`CLAUDE_CONFIG_DIR`), which is what decides the account:

- **Named accounts** — add an account (name + config dir; claudectl creates it and can open `/login` right away), rename it, switch the active one, or **open it in a new terminal with one key** so both accounts run **at the same time**.
- **Per-launch account** — the launch-options screen has an **Account** field: pick which account this specific session starts under, without changing your default.
- **All accounts in the usage bar** — the plan-usage banner shows **one bar per account** (labeled by email/name) and updates dynamically, so you see every account's session/weekly limits at a glance. A single account stays a single compact bar.
- **One row per project, not per account** — if the same folder has sessions under two accounts, the project list shows a single row (default account primary, tagged `[+other-account]`) instead of a duplicate. Opening it merges every account's sessions into one list, foreign-account sessions marked inline (`[account-name]`); rename/archive/delete/fork/view all act on that session's own account, and resuming one launches under the right account automatically.
- **Inject context across accounts** (`⇧K` in the sessions menu) — start a new session seeded with the transcript of any prior session for this project, including ones from a different account.
- **Account-accurate memory** — the memory graph lives under the project's real path (shared by every account), and the features that feed it now read **every** account's sessions: lesson extraction, the CLAUDE.md session-topics block, per-project usage stats, workspace freshness counts, and the recent-sessions quick-resume list. A project used under two accounts is one merged row in the usage dashboard, not two.

### Plan→Execute — two models, one task (`⇧X`)
Plan with an accurate model, execute with a cheaper/faster one — big token savings for the same result. claudectl plans the task headlessly with `plan_model` (default Opus 4.8), shows you the plan to approve/reject, saves it to `.claudectl/plan-latest.md`, then launches an interactive session on `exec_model` (default Sonnet 5) seeded to read and execute that plan. Expensive reasoning happens once; the build runs on the cheap tier. Nobody else orchestrates this from the launcher.

### Adaptive agent selection (`g`)
The agents screen opens with a **"Suggested for this project"** section — library agents ranked against the project's languages (from the dependency graph), memory entities, and name. Local scoring, instant, free. Setting `agents_auto: 'auto'` applies suggestions automatically on first open (your manual picks are never touched).

### Token economy — shrink the per-turn cost
CLAUDE.md and memory files ride in the model's context on **every** message, so their size is a permanent per-turn tax. claudectl makes that cost visible and cuts it:

- **Context weight audit (`⇧W`)** — one screen estimating the tokens auto-loaded on every turn for this project: CLAUDE.md broken into its blocks (manual / autogen / session topics / memory digest), the global `~/.claude/CLAUDE.md`, `.claude/rules/*` (marked *lazy* when glob-scoped, so they cost nothing until a matching file is touched), `system-prompt.txt`, SessionStart hook injections, and MCP servers — with a running always-on total and inline warnings (CLAUDE.md over 200 lines, an unbounded session-topics block, a global CLAUDE.md that loads in every project).
- **Prune the unbounded bits (`p` in the audit)** — the CLAUDE.md session-topics log used to grow forever; it's now capped to the most recent N entries (`claude_md_sessions_cap`, default 10) and the autogen commit list is configurable (`claude_md_commits`). Prune rebuilds them in place without touching your manual prose or the memory block.
- **Compress CLAUDE.md with AI (`⇧C`)** — rewrites the hand-written part into a lean lookup-table style (targets under 500 tokens), shows a before→after token count and a git-style diff to approve, keeps a `CLAUDE.md.bak`, and preserves the machine-maintained blocks verbatim.
- **Launch economy controls** — the launch-options screen adds a **Think cap** (`MAX_THINKING_TOKENS`) and **Subagents** model (`CLAUDE_CODE_SUBAGENT_MODEL`) field, plus an **`e` economy preset** (Sonnet · 8k thinking cap · Haiku subagents) in one key. Set defaults in Settings or per project.
- **Deny heavy reads (`d` in the audit)** — scans the project and writes `permissions.deny` rules (`node_modules/**`, `dist/**`, lockfiles, …) into the project's `.claude/settings.json` so a stray read can't pull thousands of tokens of generated content into context. Merges without clobbering existing settings.
- **Token-saver hooks** — `concise-output` (a SessionStart rule: no narration, no re-printed code) and `filter-test-output` (rewrites `pytest`/`npm test`/`go test` commands to pipe through a failures-only filter before the output hits context) join the hooks manager alongside the existing code-minimization hook.
- **Compact instructions** — scaffolded/AI-generated CLAUDE.md now includes a `# Compact instructions` section that steers Claude Code's auto-compaction toward what matters; the audit offers to add one (`i`) if it's missing.

### Daily token tracking (⚙ Usage stats → `d`)
Per-day table of the last 14 days — tokens in/out/cache, est. cost, sessions, bar chart, today highlighted, live plan-window % alongside. Optional `daily_token_alert` badge on the main screen when today's tokens cross your threshold.

### Workspace provenance & freshness
- **Provenance manifest** — `<project>/.claudectl/workspace-manifest.json` records where generated context came from: repo HEAD, source-file hashes (CLAUDE.md/README/configs), sessions analyzed (count + range), CLAUDE.md files, MCP server snapshots + tool counts, and last-run timestamps for scaffold / AI-analyze / launch. Updated automatically after those operations (best-effort — never blocks them).
- **Freshness check** — `claudectl workspace status` (run inside a repo) or `w` in the sessions menu shows 🟢 Fresh / 🟡 Stale / 🔴 Invalid per component and an overall freshness score. Detects when the repo HEAD moved, README changed, or new sessions accrued since the memory was generated, plus a `safe_to_launch` flag. Status is read-only — viewing never mutates the manifest.
- **Change diffs** — when AI-regenerating CLAUDE.md (`a`) or a system prompt (`s`), the approval step shows a **git-style colored diff** (old → new) so you decide *before* writing (`f` toggles to the full proposed text; ENTER approve, ESC reject). The previous version is snapshotted under `.claudectl/snapshots/`, so the workspace screen (`w`) lists recent changes with `+/−` counts and re-opens the last diff on `c` (CLAUDE.md) / `s` (system prompt).

### Hooks
- **19 ready-made templates** — one-key install, toggle, or remove (edits `settings.json` safely). Formatting (Prettier, Ruff, ESLint, gofmt), safety guardrails that **block** dangerous tools (`rm -rf`, `git reset --hard`, force-push, sudo, curl; reading `.env`; writing secrets — exit-code-2 blocks), audit/notify (log Bash commands, beep on finish / when input is needed), context injection (git status at session start; a compact **code-minimization** rule that curbs over-engineering — inspired by [Ponytail](https://github.com/DietrichGebert/ponytail)), and **token savers** (`concise-output` trims narration and re-printed code; `filter-test-output` pipes test runs through a failures-only filter before the output enters context). Guards/blocks run as bundled Python (shell-agnostic); formatters no-op when the tool is absent.
- **AI-generate a hook** — describe what you want in plain language; Claude returns a validated hook spec (event + matcher + command) you preview and confirm before it's saved.
- **Remove broken/legacy hooks** — one action purges hook commands that error under a bash hook shell.

### Usage analytics
- **Usage stats dashboard** — tokens (in/out/cache) and estimated cost per project and per session, parsed from local transcripts; cached for instant reopening
- **Plan usage** — daily/weekly limit bars with reset times shown on the main screen

### Per-project launch control
- **Effort / model / permissions / agent** — reasoning effort, model override, `--permission-mode`, and `--agent` before each launch; effort/model/permission remembered per project
- **New-session options** — name the session (`-n`) and launch in a git worktree (`-w`)
- **Extra PATH entries** / **Add directories** — per-project PATH dirs and `--add-dir` context roots

### Quality of life
- **Themes (17)** — switch palette in Settings (live preview, cursor stays on the selection): default, ocean, forest, mono, ember (red), plus Catppuccin Mocha, Catppuccin Latte, Tokyo Night, Dracula, Nord, Gruvbox, Rosé Pine, Kanagawa, Everforest, Ayu, Monokai Pro, Solarized
- **AI session titles** — unnamed sessions show their AI-generated transcript title
- **Settings screen** (⚙) — editor, claude.exe path, **config dir / account** (`CLAUDE_CONFIG_DIR`), theme, and default launch options (`~/.claude/claudectl.json`)
- **Confirm dialogs & multi-select** — modern yes/no and checkbox pickers throughout; command keys accent-colored on every screen
- **Help screen** — press `?` for a keyboard reference

---

## Install

### Requirements

- Python 3.10+
- Windows 10 or Windows 11
- [Claude Code CLI](https://docs.anthropic.com/claude-code) installed (auto-detected at `%USERPROFILE%\.local\bin\claude.exe` or on PATH; overridable in Settings)
- Any text editor — Notepad++ / VS Code are auto-detected, Windows Notepad is the fallback (overridable in Settings)

### Setup

**Option A — pipx (recommended)**

```
pipx install claudectl
claudectl
```

That's it — `claudectl` launches the session browser and starts Claude directly.

**Option B — clone and run**

```
git clone https://github.com/babarmuhammad/claudectl.git
cd claudectl
```

Double-click `Open Repo cmd.bat` (or run it from a terminal).

<details>
<summary>Optional: Desktop shortcut & taskbar pin</summary>

**Desktop shortcut** — right-click `Open Repo cmd.bat` → **Send to** → **Desktop (create shortcut)**.

**Pin to taskbar (Windows 11)** — Windows 11 can't pin `.bat` shortcuts directly; the shortcut must point to `cmd.exe`. Run this once in PowerShell from the repo folder:

```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Open Repo Claude.lnk")
$lnk.TargetPath       = "C:\Windows\System32\cmd.exe"
$lnk.Arguments        = "/c `"$PWD\Open Repo cmd.bat`""
$lnk.WorkingDirectory = "$PWD"
$lnk.IconLocation     = "$PWD\claudectl.ico, 0"
$lnk.Save()
```

Then right-click the Desktop shortcut → **Pin to taskbar**.

**Elevated shortcut, no repeated UAC prompt** — if `claude.exe` or your project paths need admin rights, a plain "Run as administrator" shortcut checkbox triggers a UAC prompt on every launch. To elevate once and skip the prompt afterward, register a Scheduled Task that already runs at highest privilege, then point the shortcut at `schtasks /run`:

```powershell
# 1) register the task (one-time)
$action    = New-ScheduledTaskAction -Execute "C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\wt.exe" -Argument '-d "<repo>" powershell -Command "& ''<repo>\Open Repo cmd.bat''"' -WorkingDirectory "<repo>"
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "ClaudeCtl" -Action $action -Principal $principal -Settings $settings -Force

# 2) point the shortcut at the task instead of launching directly
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\claudectl.lnk")
$lnk.TargetPath       = "C:\Windows\System32\schtasks.exe"
$lnk.Arguments        = '/run /tn "ClaudeCtl"'
$lnk.WorkingDirectory = "<repo>"
$lnk.IconLocation     = "<repo>\claudectl.ico, 0"
$lnk.Save()
```

Leave the shortcut's own **"Run as administrator"** checkbox unticked — `schtasks.exe` itself doesn't need to be elevated, only the task it triggers. Launching via `wt.exe` (instead of `cmd.exe`/`powershell.exe` directly) also avoids the legacy-conhost fallback that elevated console apps can trigger, which otherwise makes the TUI render with broken colors/box-drawing under UAC.

</details>

### Installing the agent library

The **⚙ Agents** screen reads `~/.claude/claudectl-agents/<category>/*.md`. To bulk-install the [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) catalog (154 agents, mirrored by category), run this PowerShell snippet once:

> The agent catalog is created and maintained by **[VoltAgent](https://github.com/VoltAgent)** — [awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents). claudectl only mirrors it into the library; all credit for the agents goes to the original authors. Please refer to that repository for its license and contribution terms.

```powershell
$repo = 'https://api.github.com/repos/VoltAgent/awesome-claude-code-subagents/contents/categories'
$raw  = 'https://raw.githubusercontent.com/VoltAgent/awesome-claude-code-subagents/main/categories'
$lib  = "$env:USERPROFILE\.claude\claudectl-agents"
foreach ($cat in (Invoke-RestMethod $repo | Where-Object { $_.type -eq 'dir' }).name) {
    $dir = Join-Path $lib $cat
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($f in (Invoke-RestMethod "$repo/$cat") | Where-Object { $_.name -like '*.md' -and $_.name -ne 'README.md' }) {
        Invoke-WebRequest "$raw/$cat/$($f.name)" -OutFile (Join-Path $dir $f.name)
    }
    Write-Host "$cat done"
}
```

Install a **single** agent directly into the library (e.g. into `09-meta-orchestration`):

```bash
curl -sL https://raw.githubusercontent.com/VoltAgent/awesome-claude-code-subagents/main/categories/09-meta-orchestration/agent-installer.md \
  -o "$USERPROFILE/.claude/claudectl-agents/09-meta-orchestration/agent-installer.md"
```

These land in the library (not `~/.claude/agents/`), so they don't bloat every Claude session — claudectl copies only the ones you select for a project into that project's `.claude/agents/` (`g` in the sessions menu).

---

## Usage

### Main screen

On launch, claudectl shows all projects Claude Code has ever opened, sorted by most recently used.

- Quick-resume items appear at the top (★ = most recent session, ☆ = older sessions). These are the 5 most recently used sessions across all projects; selecting one resumes that exact session without navigating into the project's list.
- All other projects follow, sorted by recency — type to filter live
- The MCP status footer shows connected MCP servers once the background check completes
- Bottom menu: **🔍 Search all sessions**, **⚙ Usage stats**, **⚙ MCP servers**, **⚙ Agents**, **⚙ Hooks**, **⚙ Global CLAUDE.md**, **⚙ Settings**, **? Help**

### Built-in screens

**🔍 Search all sessions** — indexes session names, AI titles, and previews across every project (cached — instant after the first scan). Type to filter, ENTER resumes the selected session directly, no matter which project it belongs to.

**⚙ Usage stats** — per-project table of sessions, messages, tokens (in / out / cache) and estimated API-equivalent cost, parsed from local transcripts. ENTER drills into per-session rows. Costs are estimates at published API rates — useful as a value/consumption gauge if you're on a subscription plan. First scan shows progress and can be stopped with ESC (partial results); later opens are instant thanks to a persistent cache.

**⚙ Global CLAUDE.md / MCP Analysis** — lists all connected MCP servers; select one to run Claude with a prompt that calls the MCP's `tools/list` endpoint and formats the result as markdown, written into `~/.claude/CLAUDE.md` inside a per-server sentinel block (cleanly re-updatable). You can also open the global CLAUDE.md directly in your editor from this menu. See [Global CLAUDE.md](#global-claudemd).

### Key bindings

**Main screen (project list)**

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| ENTER | Select project / resume / open menu item |
| Type text | Filter projects live |
| ESC | Clear filter, then exit |

**Sessions screen (session list for a project)**

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| ENTER | Select / confirm |
| ESC | Back / cancel (clears filter first if active) |
| r | Rename session |
| d | Archive or delete session |
| f | Fork session |
| v | View transcript |
| e | Export transcript to markdown |
| i | Session info (tokens, cost, models, branch) |
| F | Changed files (from session tool calls) |
| t | Tag session |
| u | Project usage stats |
| m | Memory hub (build · ask · preview injection · lessons · toggles) |
| L | Lessons review (approve / pin / evict session learnings) |
| / | Action palette — every action, type-to-filter |
| ! | One-key project setup (first open: CLAUDE.md + memory + rules) |
| M | Memory map (CLAUDE.md hierarchy) |
| A | Toggle archived sessions view |
| c | Scaffold CLAUDE.md (git + sessions) |
| a | AI-generate CLAUDE.md (Claude CLI) |
| s | Edit / generate system prompt |
| g | Pick project agents (library checklist → `.claude/agents/`) |
| n | Architecture graph + project memory screen (then `o` open graph · `m` build memory · `a` ask · `r` rebuild) |
| w | Workspace status (provenance & freshness) |
| ⇧K | New chat seeded with context from another session (any account) |
| ⇧W | Context weight audit — token cost of everything auto-loaded per turn |
| ⇧C | Compress CLAUDE.md with AI (cut per-turn tokens) |
| p | Manage extra PATH entries |
| x | Manage --add-dir directories |
| ? | Help / keyboard reference |
| BACKSPACE | Delete last filter character |
| Type text | Filter sessions live by name or preview |

**Transcript viewer (`v`)**

| Key | Action |
|-----|--------|
| ↑ / ↓ | Scroll line by line |
| ← / → / SPACE | Page up / down |
| / | Search inside the conversation |
| n / p | Jump to next / previous match (wraps) |
| i | Toggle session info header (tokens, cost, models, branch) |
| e | Export to markdown |
| ESC | Clear search, then exit |

The footer shows your position as `msg N/M` — counting conversation messages, not raw lines.

**Launch options screen**

| Key | Action |
|-----|--------|
| ↑ / ↓ | Switch fields (Effort / Model / Permissions / Lead agent / Account / Think cap / Subagents / Worktree / Name) |
| ← / → | Cycle values; edit Name/Worktree |
| e | Economy preset (Sonnet · 8k thinking cap · Haiku subagents) |
| ENTER | Launch with selected options |
| ESC | Back to main menu (no launch) |

Worktree & Name appear only for new sessions; Lead agent appears when `~/.claude/agents/` has agents; Account appears when you've added extra accounts. **Think cap** sets `MAX_THINKING_TOKENS` and **Subagents** sets `CLAUDE_CODE_SUBAGENT_MODEL` for the launched session. Project agents picked with `g` are shown read-only here.

**Multi-select / confirm**

- Checkbox pickers (MCP tools, agent tools): `SPACE` toggle, `a` all, `n` none, `v` view (agent `.md`, where available), `ENTER` confirm, `ESC` cancel.
- Confirm dialogs: `←→` choose, `ENTER` confirm, `ESC`/`y`/`n`.

---

## Reference

### Per-project files

Each project gets a folder at `~/.claude/projects/<encoded-name>/`. claudectl reads and writes several files there:

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

The agent library lives at `~/.claude/claudectl-agents/<category>/*.md` (account-wide, not auto-loaded); selecting agents for a project copies them into that project's `.claude/agents/`. A single lead agent can also come from `~/.claude/agents/`. Hooks and MCP servers are stored in `settings.json` / managed via `claude mcp`.

### Workspace status

claudectl tracks the **provenance and freshness** of the context it generates. After scaffold, AI-analyze, or launch, it writes `<project>/.claudectl/workspace-manifest.json` (falling back to the encoded `~/.claude/projects/<encoded>/.claudectl/` folder if the working dir is read-only). The manifest is schema-versioned and forward-compatible — old files load, unknown keys survive round-trips.

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

…or press `w` in the sessions menu for the same view as a TUI screen (`r` refreshes, ESC exits). Indicators: 🟢 Fresh · 🟡 Stale · 🔴 Invalid. A component goes **stale** when the repo HEAD moved, README/source hashes changed, or new sessions accrued since the memory was generated; **invalid** means a missing-after-generation CLAUDE.md or a corrupt manifest. `safe_to_launch` is false only when an invalid check is present. The freshness score is the weighted fraction of applicable checks that are fresh. Viewing status is **read-only** — it never rewrites the manifest.

### CLAUDE.md auto-generation

**`c` — Scaffold (fast, mechanical)** builds CLAUDE.md from:

- Git repos found up to 2 levels deep in the project and any linked extra paths
- Last 7 commits from each repo (`git log --oneline -7`)
- First 15 lines of each repo's README
- All session topics (accumulated, never discarded)

On an existing file, only the `<!-- AUTOGEN:START -->…<!-- AUTOGEN:END -->` and `<!-- SESSIONS:START -->…<!-- SESSIONS:END -->` blocks are replaced. Everything outside those blocks is preserved exactly.

**`a` — AI analyze (slower, comprehensive)** runs `claude.exe -p` with a rich prompt containing the full directory tree, git history, READMEs, extra paths, and session history. Claude writes the entire CLAUDE.md. You review it in a pager and approve or reject before any file is written.

On an existing file, the current content is passed as ground truth with instructions to update only facts that have clearly changed. After generation the `<!-- AUTOGEN:START/END -->` and `<!-- SESSIONS:START/END -->` blocks are injected mechanically, and `<!-- AI:ANALYZED -->` is inserted on line 2 so future runs enter update mode rather than fresh mode.

### Global CLAUDE.md

`~/.claude/CLAUDE.md` is loaded by Claude Code in every session across all projects. claudectl uses it to store MCP tool documentation. Each MCP server gets its own sentinel-delimited section:

```
<!-- MCP:Notion:START -->
## MCP: Notion
… tool listing …
<!-- MCP:Notion:END -->
```

Re-running the analysis for the same server updates only that section; other content is untouched. Access via: main screen → **⚙ Global CLAUDE.md / MCP Analysis**.

### Session encoding

Claude Code encodes project paths as folder names under `~/.claude/projects/` by replacing path separators with `--` and certain special characters with `-`. For example:

```
D:\Projects\my-app  →  D--Projects-my-app
```

claudectl's `find_actual_path()` in `paths.py` reverses this by walking the filesystem and matching encoded components, handling edge cases like `_`, `+`, `-`, `#` in directory names.

### File layout

```
.\claudectl\
├── claude-sessions.py      # launcher stub: applies theme, --launch, crash handler
├── Open Repo cmd.bat       # bat launcher (runs TUI, then py --launch)
├── pyproject.toml
├── README.md
└── claude_sessions\        # package
    ├── config.py           # constants, paths, themes, settings
    ├── paths.py            # encode_component, find_actual_path
    ├── sessions.py         # session parsing + persistence helpers
    ├── main.py             # run() — project discovery, launch flow, main loop
    ├── render.py           # frame-diff renderer, layout + hint helpers
    ├── ui.py               # menu, pager, multiselect, confirm, launch options, settings
    ├── session_menu.py     # per-project sessions menu
    ├── search.py           # cross-project session search
    ├── transcript.py       # transcript viewer + markdown export
    ├── stats.py            # usage stats dashboard
    ├── usage.py            # plan usage limit bars
    ├── mcp.py              # MCP manager + background status poll
    ├── agents.py           # agent library, per-project selection, scaffold/AI
    ├── hooks.py            # hooks template / toggle / remove
    ├── workspace.py        # provenance manifest + freshness status
    ├── connections.py      # project connections graph + plexus HTML
    ├── memory.py           # Claude-powered semantic memory (ECL + ask)
    ├── diffview.py         # git-style diffs for generated files
    ├── claude_md.py        # scaffold + AI CLAUDE.md, memory map
    └── system_prompt.py    # edit / AI-generate system prompt
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "claude.exe not found" screen on startup | Install [Claude Code](https://docs.anthropic.com/claude-code), or set the path in **⚙ Settings** |
| Generated files don't open in an editor | Set your editor path in **⚙ Settings** (auto-detects Notepad++, VS Code, falls back to Notepad) |
| Window closes instantly with an error | Check `%TEMP%\claudectl_crash.log` — the crash handler writes the traceback there |
| Projects missing from the list | The project folder was moved/deleted, or the path can't be decoded — see [Session encoding](#session-encoding) |
| Wrong account / want a second account | Set **Config dir** in **⚙ Settings** to that account's `CLAUDE_CONFIG_DIR` (e.g. `~/.claude-work`). Drives both session browsing and the env handed to `claude` at launch. Blank = default `~/.claude`. Restart claudectl to apply. One config dir active at a time. |
| Settings location | `~/.claude/claudectl.json` — safe to edit by hand or delete to reset (always read from `~/.claude`, independent of Config dir) |
| Usage stats look stale | Delete `~/.claude/claudectl-stats-cache.json` — it rebuilds on the next scan |
