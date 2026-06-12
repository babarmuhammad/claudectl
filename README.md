# claudectl

A Windows workspace manager for Claude Code — project memory, MCP awareness, and multi-project workflows in a fast terminal UI.

Claude Code treats your work as a collection of chats. claudectl treats each project as a **persistent workspace**: sessions stay browsable and searchable, project context lives in maintained CLAUDE.md files, MCP servers are visible at a glance, and every launch is configured per project. Switching projects stops feeling like losing the agent's memory.

---

## Features

**Session management**
- **Session browser** — every Claude Code project and session, sorted by recency
- **Quick-resume** — ★/☆ shortcuts on the main screen jump straight back into recent sessions across all projects
- **Search** — type to filter sessions live; **🔍 Search all sessions** finds and resumes any session across every project
- **Transcript viewer & export** — read any session in a pager (`v`) with full-text search inside the conversation (`/`, `n`/`p` to jump between matches) and a message-position counter; export to markdown (`e`)
- **Session info** — per-session tokens, est. cost, models, git branch, duration (`i`)
- **Archive** — move sessions to a restorable `archived/` folder instead of deleting (`d`, toggle view with `A`)
- **Rename / Fork / Continue** — rename (`r`), fork (`f`), or continue the latest session (`claude -c`)

**Project memory**
- **Scaffold CLAUDE.md** (C) — build project context mechanically from git repos, recent commits, READMEs, and prior session topics
- **AI CLAUDE.md generation** (A) — Claude itself deep-analyzes the codebase and writes or updates a comprehensive CLAUDE.md; you review before anything is written
- **System prompts** (S) — AI-generate or hand-edit a per-project system prompt injected on every launch

**MCP awareness**
- **MCP status** — connected servers shown in the footer on startup
- **MCP documentation** — analyze any MCP server's tools and write the docs into the global `~/.claude/CLAUDE.md` so Claude knows them in every session

**Usage analytics**
- **Usage stats dashboard** — tokens (in/out/cache) and estimated cost per project and per session, parsed from local transcripts; cached for instant reopening

**Per-project launch control**
- **Effort / model / permissions** — reasoning effort, model override, and `--permission-mode` preset before each launch; last choice remembered per project
- **New-session options** — name the session (`-n`) and launch in a git worktree (`-w`)
- **Extra PATH entries** — per-project PATH dirs injected into Claude's environment
- **Add directories** — per-project `--add-dir` list for extra context roots

**Quality of life**
- **AI session titles** — sessions without a manual name show their AI-generated transcript title
- **Settings screen** (⚙) — configure editor, claude.exe path, **config dir / account** (`CLAUDE_CONFIG_DIR`), and default launch options (`~/.claude/claudectl.json`)
- **Help screen** — press `?` for a keyboard reference

---

## Requirements

- Python 3.10+
- Windows 10 or Windows 11
- [Claude Code CLI](https://docs.anthropic.com/claude-code) installed (auto-detected at `%USERPROFILE%\.local\bin\claude.exe` or on PATH; overridable in Settings)
- Any text editor — Notepad++ / VS Code are auto-detected, Windows Notepad is the fallback (overridable in Settings)

---

## Setup

### Option A — pipx (recommended)

```
pipx install claudectl
claudectl
```

That's it — `claudectl` launches the session browser and starts Claude directly.

### Option B — clone and run

```
git clone https://github.com/babarmuhammad/claudectl.git
cd claudectl
```

Double-click `Open Repo cmd.bat` (or run it from a terminal).

### Optional: Desktop shortcut

Right-click `Open Repo cmd.bat` → **Send to** → **Desktop (create shortcut)**.

### Optional: Pin to taskbar (Windows 11)

Windows 11 can't pin `.bat` shortcuts directly — the shortcut must point to `cmd.exe`. Run this once in PowerShell from the repo folder:

```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Open Repo Claude.lnk")
$lnk.TargetPath       = "C:\Windows\System32\cmd.exe"
$lnk.Arguments        = "/c `"$PWD\Open Repo cmd.bat`""
$lnk.WorkingDirectory = "$PWD"
$lnk.IconLocation     = "$PWD\claude folder.ico, 0"
$lnk.Save()
```

Then right-click the Desktop shortcut → **Pin to taskbar**.

---

## Usage

### Main screen

On launch, claudectl shows all projects Claude Code has ever opened, sorted by most recently used.

- Quick-resume items appear at the top (★ = most recent session, ☆ = older sessions)
- All other projects follow, sorted by recency — type to filter live
- The MCP status footer shows connected MCP servers once the background check completes
- Bottom menu: **🔍 Search all sessions**, **⚙ Usage stats**, **⚙ Global CLAUDE.md / MCP Analysis**, **⚙ Settings**, **? Help**

### 🔍 Search all sessions

Indexes session names, AI titles, and previews across every project (cached — instant after the first scan). Type to filter, ENTER resumes the selected session directly, no matter which project it belongs to.

### ⚙ Usage stats

Per-project table of sessions, messages, tokens (in / out / cache) and estimated API-equivalent cost, parsed from local transcripts. ENTER drills into per-session rows. Costs are estimates at published API rates — useful as a value/consumption gauge if you're on a subscription plan. First scan shows progress and can be stopped with ESC (partial results); later opens are instant thanks to a persistent cache.

### Quick-resume items (★ / ☆)

These are the 5 most recently used sessions across all projects. Selecting one immediately resumes that exact session without navigating into the project's session list. ★ marks the single most recent session; ☆ marks older entries.

### ⚙ Global CLAUDE.md / MCP Analysis

Opens a sub-menu listing all connected MCP servers. Select any server to run Claude with a prompt that calls the MCP's `tools/list` endpoint and formats the result as markdown. The output is written into `~/.claude/CLAUDE.md` inside a per-server sentinel block so it can be cleanly updated on subsequent runs. You can also open the global CLAUDE.md directly in your editor from this menu.

---

## Key Bindings

### Main screen (project list)

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| ENTER | Select project / resume / open menu item |
| Type text | Filter projects live |
| ESC | Clear filter, then exit |

### Sessions screen (session list for a project)

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
| u | Project usage stats |
| A | Toggle archived sessions view |
| c | Scaffold CLAUDE.md (git + sessions) |
| a | AI-generate CLAUDE.md (Claude CLI) |
| s | Edit / generate system prompt |
| p | Manage extra PATH entries |
| x | Manage --add-dir directories |
| ? | Help / keyboard reference |
| BACKSPACE | Delete last filter character |
| Type text | Filter sessions live by name or preview |

### Transcript viewer (`v`)

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

### Launch options screen

| Key | Action |
|-----|--------|
| ↑ / ↓ | Switch fields (Effort / Model / Permissions / Worktree / Name) |
| ← / → | Cycle values; edit Name/Worktree |
| ENTER | Launch with selected options |
| ESC | Back to main menu (no launch) |

---

## Per-project files

Each project gets a folder at `~/.claude/projects/<encoded-name>/`. claudectl reads and writes several files there:

| File | Purpose |
|------|---------|
| `<session-id>.jsonl` | Claude Code session transcript (managed by Claude Code) |
| `<session-id>.name` | Custom display name you set with r |
| `extra-paths.txt` | Additional PATH directories added when launching Claude |
| `add-dirs.txt` | Directories passed via `--add-dir` on every launch |
| `system-prompt.txt` | System prompt injected via `--system-prompt-file` on every launch |
| `archived/` | Archived sessions (restorable from the A view) |

---

## CLAUDE.md auto-generation

### C — Scaffold (fast, mechanical)

Builds CLAUDE.md from:

- Git repos found up to 2 levels deep in the project and any linked extra paths
- Last 7 commits from each repo (`git log --oneline -7`)
- First 15 lines of each repo's README
- All session topics (accumulated, never discarded)

On an existing file, only the `<!-- AUTOGEN:START -->…<!-- AUTOGEN:END -->` and `<!-- SESSIONS:START -->…<!-- SESSIONS:END -->` blocks are replaced. Everything outside those blocks is preserved exactly.

### A — AI analyze (slower, comprehensive)

Runs `claude.exe -p` with a rich prompt containing the full directory tree, git history, READMEs, extra paths, and session history. Claude writes the entire CLAUDE.md. You review it in a pager and approve or reject before any file is written.

On an existing file, the current content is passed as ground truth with instructions to update only facts that have clearly changed.

After generation the `<!-- AUTOGEN:START/END -->` and `<!-- SESSIONS:START/END -->` blocks are injected mechanically, and `<!-- AI:ANALYZED -->` is inserted on line 2 so future runs enter update mode rather than fresh mode.

---

## Global CLAUDE.md

`~/.claude/CLAUDE.md` is loaded by Claude Code in every session across all projects. claudectl uses it to store MCP tool documentation:

Each MCP server gets its own sentinel-delimited section:

```
<!-- MCP:Notion:START -->
## MCP: Notion
… tool listing …
<!-- MCP:Notion:END -->
```

Re-running the analysis for the same server updates only that section; other content is untouched.

Access via: main screen → **⚙ Global CLAUDE.md / MCP Analysis**

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "claude.exe not found" screen on startup | Install [Claude Code](https://docs.anthropic.com/claude-code), or set the path in **⚙ Settings** |
| Generated files don't open in an editor | Set your editor path in **⚙ Settings** (auto-detects Notepad++, VS Code, falls back to Notepad) |
| Window closes instantly with an error | Check `%TEMP%\claudectl_crash.log` — the crash handler writes the traceback there |
| Projects missing from the list | The project folder was moved/deleted, or the path can't be decoded — see *Session encoding* below |
| Wrong account / want a second account | Set **Config dir** in **⚙ Settings** to that account's `CLAUDE_CONFIG_DIR` (e.g. `~/.claude-work`). Drives both session browsing and the env handed to `claude` at launch. Blank = default `~/.claude`. Restart claudectl to apply. One config dir active at a time. |
| Settings location | `~/.claude/claudectl.json` — safe to edit by hand or delete to reset (always read from `~/.claude`, independent of Config dir) |
| Usage stats look stale | Delete `~/.claude/claudectl-stats-cache.json` — it rebuilds on the next scan |

---

## Session encoding

Claude Code encodes project paths as folder names under `~/.claude/projects/` by replacing path separators with `--` and certain special characters with `-`. For example:

```
D:\Projects\my-app  →  D--Projects-my-app
```

claudectl's `find_actual_path()` in `paths.py` reverses this by walking the filesystem and matching encoded components, handling edge cases like `_`, `+`, `-`, `#` in directory names.

---

## File layout

```
.\claudectl\
├── claude-sessions.py     # launcher stub + crash handler
├── Open Repo cmd.bat      # bat launcher
├── README.md
├── .gitignore
└── claude_sessions\       # package
    ├── __init__.py
    ├── config.py          # constants, paths, sentinel strings
    ├── paths.py           # encode_component, find_actual_path
    ├── sessions.py        # session parsing, persistence helpers
    ├── ui.py              # text_input, menu, paths_menu, launch_options_menu
    ├── claude_md.py       # scaffold_claude_md, ai_scaffold_claude_md, helpers
    ├── system_prompt.py   # edit_system_prompt, ai_generate_system_prompt
    ├── session_menu.py    # sessions_menu
    ├── mcp.py             # MCP background poll, global_claude_md_menu
    └── main.py            # run() — project discovery and main loop
```
