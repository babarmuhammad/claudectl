# claudectl

Windows terminal UI for managing Claude Code sessions.

---

## Features

- **Session browser** — lists all Claude Code projects and their sessions sorted by recency
- **Quick-resume** — ★ (most recent) and ☆ (older) shortcuts at the top of the main screen for instant session resume
- **Type-to-filter** — type any text in the sessions menu to filter live by session name or preview
- **Rename / Delete / Fork** — manage individual sessions with R / D / F keys
- **AI CLAUDE.md generation** — press A to run Claude CLI to deeply analyze your project and write or update CLAUDE.md
- **Scaffold CLAUDE.md** — press C to build CLAUDE.md from git repos, recent commits, READMEs, and session topics
- **System prompt generation** — press S to AI-generate or manually edit a per-project system prompt
- **MCP status** — background-polls `claude mcp list` on startup; connected servers shown in footer
- **Global CLAUDE.md** — ⚙ menu item to analyze any MCP server's tools and write documentation into `~/.claude/CLAUDE.md`
- **Effort / model selector** — choose thinking effort level and model override before launching each session
- **Extra PATH entries** — per-project additional PATH dirs injected when launching Claude

---

## Requirements

- Python 3.10+
- Windows 10 or Windows 11
- [Claude Code CLI](https://docs.anthropic.com/claude-code) installed (`claude.exe` at `%USERPROFILE%\.local\bin\claude.exe`)
- Notepad++ installed at `C:\Program Files\Notepad++\notepad++.exe` (used to open generated files; failures are silently ignored)

---

## Setup

### File placement

Place the entire `D:\Claude\` directory as-is. The package lives at `D:\Claude\claude_sessions\`.

### .bat shortcut

`Open Repo cmd.bat` (already present in this directory) calls:

```bat
py "%~dp0claude-sessions.py"
```

This invokes the launcher stub which imports and runs `claude_sessions.main.run()`.

### Desktop shortcut (.lnk)

Create a shortcut to `Open Repo cmd.bat` on the Desktop:

1. Right-click `Open Repo cmd.bat` → Send to → Desktop (create shortcut)
2. Right-click the shortcut → Properties → set **Run** to *Minimized* or *Normal* as preferred
3. Optionally assign a hotkey in the shortcut properties

### Pin to taskbar (Windows 11)

Windows 11 blocks pinning `.bat` shortcuts directly. Workaround — point the shortcut to `cmd.exe` instead:

1. Right-click the Desktop shortcut → **Properties**
2. Set **Target** to:
   ```
   C:\Windows\System32\cmd.exe /c "D:\Claude\Open Repo cmd.bat"
   ```
3. Set **Start in** to `D:\Claude`
4. Click **Change Icon** → browse to `D:\Claude\claude folder.ico`
5. Click OK → right-click the shortcut → **Pin to taskbar**

Or run this PowerShell snippet to rebuild the shortcut automatically:
```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Open Repo Claude.lnk")
$lnk.TargetPath       = "C:\Windows\System32\cmd.exe"
$lnk.Arguments        = "/c `"D:\Claude\Open Repo cmd.bat`""
$lnk.WorkingDirectory = "D:\Claude"
$lnk.IconLocation     = "D:\Claude\claude folder.ico, 0"
$lnk.Save()
```

Then right-click the Desktop shortcut → **Pin to taskbar**.

---

## Usage

### Main screen

On launch, claudectl shows all projects Claude Code has ever opened, sorted by most recently used.

- Quick-resume items appear at the top (★ = most recent session, ☆ = older sessions)
- All other projects follow, sorted by recency
- The MCP status footer shows connected MCP servers once the background check completes
- Select **⚙ Global CLAUDE.md / MCP Analysis** at the bottom to open the global context menu

### Quick-resume items (★ / ☆)

These are the 5 most recently used sessions across all projects. Selecting one immediately resumes that exact session without navigating into the project's session list. ★ marks the single most recent session; ☆ marks older entries.

### ⚙ Global CLAUDE.md / MCP Analysis

Opens a sub-menu listing all connected MCP servers. Select any server to run Claude with a prompt that calls the MCP's `tools/list` endpoint and formats the result as markdown. The output is written into `~/.claude/CLAUDE.md` inside a per-server sentinel block so it can be cleanly updated on subsequent runs. You can also open the global CLAUDE.md directly in Notepad++ from this menu.

---

## Key Bindings

### Main screen (project list)

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| ENTER | Select project |
| ESC | Exit |

### Sessions screen (session list for a project)

| Key | Action |
|-----|--------|
| ↑ / ↓ | Navigate |
| ENTER | Select / confirm |
| ESC | Back / cancel (clears filter first if active) |
| R | Rename session |
| D | Delete session (prompts for confirmation) |
| F | Fork session |
| C | Scaffold CLAUDE.md (git + sessions) |
| A | AI-generate CLAUDE.md (Claude CLI) |
| S | Edit / generate system prompt |
| P | Manage extra PATH entries |
| BACKSPACE | Delete last filter character |
| Type text | Filter sessions live by name or preview |

### Launch options screen

| Key | Action |
|-----|--------|
| ↑ / ↓ | Switch between Effort and Model fields |
| ← / → | Cycle through values for the selected field |
| ENTER | Launch with selected options |
| ESC | Launch with defaults (no effort/model override) |

---

## Per-project files

Each project gets a folder at `~/.claude/projects/<encoded-name>/`. claudectl reads and writes several files there:

| File | Purpose |
|------|---------|
| `<session-id>.jsonl` | Claude Code session transcript (managed by Claude Code) |
| `<session-id>.name` | Custom display name you set with R |
| `extra-paths.txt` | Additional PATH directories added when launching Claude |
| `system-prompt.txt` | System prompt injected via `--system-prompt-file` on every launch |

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

## Session encoding

Claude Code encodes project paths as folder names under `~/.claude/projects/` by replacing path separators with `--` and certain special characters with `-`. For example:

```
D:\Projects\my-app  →  D--Projects-my-app
```

claudectl's `find_actual_path()` in `paths.py` reverses this by walking the filesystem and matching encoded components, handling edge cases like `_`, `+`, `-`, `#` in directory names.

---

## File layout

```
D:\Claude\
├── claude-sessions.py     # launcher stub (5 lines)
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
