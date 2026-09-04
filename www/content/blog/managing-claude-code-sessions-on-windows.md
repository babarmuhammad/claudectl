---
title: Managing Claude Code sessions on Windows
description: Claude Code writes every session to a JSONL file in an encoded folder and gives you no way to browse them. What is on disk, and how to work with it.
date: 2026-09-01
tags: [claude-code, windows, sessions, workflow]
author: Babar Muhammad Anas
faq:
  - q: Where does Claude Code store sessions on Windows?
    a: Under `%USERPROFILE%\.claude\projects\<encoded-project-path>\`, one `<session-id>.jsonl` file per session, with one JSON object per line. The folder name encodes the project's real path by replacing separators with dashes, so `D:\Projects\my-app` becomes `D--Projects-my-app`.
  - q: How do I search across all my Claude Code sessions?
    a: Claude Code has no cross-session search — `/resume` only lists recent sessions in the current directory. claudectl indexes session names, AI-generated titles and previews across every project and caches the result, so you can filter live and press ENTER to resume any match regardless of which project it belongs to.
  - q: Can I rename, tag or archive a Claude Code session?
    a: Not with Claude Code alone; sessions are identified by UUID and there is no delete-safe archive. claudectl adds a display name (`<session-id>.name`), per-session tags stored in `tags.json`, and an archive that moves a session into a restorable `archived/` folder rather than deleting the transcript.
  - q: Why does my Claude Code status line show broken characters on Windows?
    a: Because Claude Code captures a status line's stdout as a pipe, so CPython picks the locale codepage — cp1252 on Windows — and a non-ASCII glyph goes out mis-encoded or raises UnicodeEncodeError outright. Any script writing a status line or hook output must call `sys.stdout.reconfigure(encoding='utf-8')` before printing.
  - q: How do I pin claudectl to the Windows 11 taskbar?
    a: Windows 11 cannot pin a `.bat` shortcut directly, so the shortcut has to point at `cmd.exe` with the batch file as an argument. Create it with WScript.Shell in PowerShell, set `IconLocation` to the bundled `.ico`, then right-click the desktop shortcut and choose Pin to taskbar.
---

## The short answer

Claude Code stores every session as a JSONL transcript under `%USERPROFILE%\.claude\projects\<encoded-project-path>\<session-id>.jsonl` and gives you no interface onto them beyond `/resume`, which lists recent sessions in the current directory only. There is no search, no naming, no tagging, no archive, and no view across projects. Managing them means either reading the JSONL yourself or putting a workspace layer in front — a browsable list per project, full-text search across every project, and per-project launch control. The Windows-specific pain is concentrated in three places: the lossy folder encoding, console encoding, and shortcut/elevation plumbing.

## What is on disk

```
%USERPROFILE%\.claude\
├── projects\
│   └── D--Projects-my-app\
│       ├── 6aff4b52-....jsonl        # the transcript
│       ├── 6aff4b52-....name         # display name (claudectl)
│       ├── tags.json                 # per-session tags (claudectl)
│       ├── system-prompt.txt         # injected on every launch (claudectl)
│       ├── add-dirs.txt              # --add-dir roots (claudectl)
│       └── archived\                 # restorable archive (claudectl)
├── settings.json                     # hooks, permissions, statusLine, outputStyle
├── CLAUDE.md                         # loaded in every session on this account
└── file-history\<session-id>\        # Claude Code's checkpoint store
```

The `.jsonl` files are the whole record: user turns, assistant turns, tool calls, tool results, one JSON object per line. They are append-only and nothing reads them back — Claude Code does not consult a prior transcript when you start a new session.

They also get big. A 2,787-message session is a normal week. Any tool that touches these must stream rather than `readlines()`; claudectl funnels every transcript read through one module (`transcripts.iter_json`) that yields objects with a `limit`/`offset`, a `max_bytes` cap, and a `prefilter` substring tested against the *raw line*, so a caller that only wants the `"Bash"` entries never pays for a `json.loads` it throws away.

## The folder name is lossy — do not decode it

`D:\Projects\my-app` encodes to `D--Projects-my-app`. Every non-alphanumeric character maps to a dash, which means the encoding is one-way. Splitting on `--` to recover a drive letter works right up until it does not:

```
\\server\share\Project   →   --server-share-Project
```

The leading `--` makes the drive-letter split yield an empty drive, and every UNC-hosted project was silently dropped from the project list, dashboard, usage and search — no error anywhere, just an absent row. This is a real bug that shipped, and the fix is instructive for anyone writing their own tooling: **every transcript line already carries the real `cwd`**. Reading it is exact for every encoding, and cheaper than the recursive directory walk it replaces. Walk the filesystem only as a fallback for a project folder with no transcripts to read.

If you are scripting against `~/.claude/projects` yourself, read `cwd` from line one of any `.jsonl` in the folder. Do not parse the folder name.

## Console encoding: the Windows tax

Two independent bugs, same root cause, both worth knowing before you write a hook or a status line.

**Writing.** Claude Code captures a hook's or status line's stdout as a *pipe*, so CPython picks the locale codepage rather than UTF-8 — cp1252 on Windows. A middle dot goes out as a bare `0xB7` and the terminal renders `Opus 5 � default �`. A block glyph like `▕` is worse: `UnicodeEncodeError`, exit 1, and a Python traceback parked under your prompt for the rest of the session. The fix is one line at the top of anything that prints:

```python
sys.stdout.reconfigure(encoding='utf-8')
```

This only *looked* fine in development because `PYTHONIOENCODING=utf-8` was set in that environment. The regression test now strips that variable before running.

**Reading.** The mirror image bites when you shell out to git. `subprocess.run(text=True)` with no `encoding=` decodes with the same codepage, so one non-ASCII branch name or path raises inside `subprocess` and the wrapper returns `None` — which surfaced to the user as "not a git repository" on a perfectly good repo. Pin it:

```python
subprocess.run(cmd, text=True, encoding='utf-8', errors='ignore')
```

claudectl routes every git call through a single function for exactly this reason; one door, one place to get the encoding right.

## Repo discovery: `.git` is not always a directory

If your workflow involves submodules or worktrees — common on Windows monorepos — this one matters. Testing `isdir('.git')` misses both. A submodule *and* a linked worktree store `.git` as a **file** containing one `gitdir:` line, and that line is the whole classifier:

| `gitdir:` contains | It is |
|---|---|
| `.git/modules/` | a submodule |
| `.git/worktrees/` | a linked worktree |

No subprocess needed to tell them apart. Getting this wrong in the obvious direction is also a trap: counting linked worktrees as repos adds every copy of a repo to its own repo list. And `git worktree list` run *inside a submodule* reports the gitdir (`…/.git/modules/<name>`), not the working directory, which quietly breaks any join from session to path.

## What a workspace layer adds

With that on-disk picture, the useful operations are the ones Claude Code does not expose. In claudectl's session menu:

| Key | Action |
|---|---|
| `/` | Action palette — every action, type-to-filter |
| `r` | Rename session (writes `<session-id>.name`) |
| `t` | Tag session (tags show inline and are searchable) |
| `f` | Fork session |
| `d` | Archive (moves to `archived/`, restorable) |
| `v` | View transcript in a pager, `/` searches inside it |
| `e` | Export the transcript to markdown |
| `i` | Session info — tokens, estimated cost, models, git branch, duration |
| `F` | Changed files, derived from the session's tool calls |
| `u` | Project usage stats |
| `⇧K` | Hand off: new session seeded with another session's context |

Two of those are worth calling out. **Changed files** (`F`) is derived from tool calls in the transcript, so it answers "what did that session actually touch" without a git diff — useful when the session ran across a branch switch. And the transcript viewer's position counter reads `msg N/M`, counting conversation messages rather than raw lines, because most lines in a `.jsonl` are tool traffic.

Cross-project search is separate from per-project filtering: **🔍 Search all sessions** indexes names, AI-generated titles and previews across every project, caches the index so later opens are instant, and resumes the match directly regardless of which project it lives in.

## Usage, read from your own transcripts

Everything needed to cost a session is already in the file — tokens in, out, cache, per model. claudectl's usage dashboard parses local transcripts (no API call) into a per-project and per-session table with estimated cost at published API rates, plus a per-day table of the last 14 days. On a subscription plan those numbers are not a bill, they are a consumption gauge — which is the right way to read them.

The plan-usage bars on the main screen are a different source: they come from the rate-limit windows Claude Code itself reports.

## Windows shell plumbing

**Pin to taskbar (Windows 11).** Windows 11 will not pin a `.bat` shortcut, so the shortcut must target `cmd.exe`:

```powershell
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut("$env:USERPROFILE\Desktop\Open Repo Claude.lnk")
$lnk.TargetPath       = "C:\Windows\System32\cmd.exe"
$lnk.Arguments        = "/c `"$PWD\Open Repo cmd.bat`""
$lnk.WorkingDirectory = "$PWD"
$lnk.IconLocation     = "$PWD\claudectl.ico, 0"
$lnk.Save()
```

Then right-click the desktop shortcut → **Pin to taskbar**.

**Elevation without a UAC prompt every time.** If `claude.exe` or your project paths need admin rights, ticking "Run as administrator" on a shortcut gives you a UAC prompt on every launch. Register a scheduled task that already runs at highest privilege and point the shortcut at `schtasks /run /tn "ClaudeCtl"` instead; leave the shortcut's own elevation checkbox *unticked*, because `schtasks.exe` does not need elevating, only the task it triggers. Launch the task through `wt.exe` rather than `cmd.exe` directly — elevated console apps otherwise fall back to legacy conhost, which renders a TUI with broken colours and box-drawing.

**No console window.** `pythonw.exe` runs the GUI without one; the full shortcut recipe including the GUI icon is in [the install guide](https://docs.claudectl.space/installation/).

**Killing a session tree.** If you script session launches, note that `taskkill /T` can report failure, and a fallback to `Popen.kill` is not optional. The POSIX equivalent has a sharper edge: `os.killpg` may only be used when the child actually leads its own process group — otherwise its group is *yours*, and the tree kill takes your own tool down with it.

## Terminal or window

The same operations exist in both interfaces over one engine. `claudectl` opens the TUI; `claudectl --gui` opens a desktop window — PyQt6 native if installed, otherwise an Edge app-mode window, otherwise your browser, served on loopback only. Neither needs a third-party Python package; claudectl is standard library only, and uses the Claude Code authentication you already have.

Full key map and command line: [docs.claudectl.space/usage](https://docs.claudectl.space/usage/).
