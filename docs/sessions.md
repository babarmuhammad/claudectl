---
description: >-
  Browse, search, tag, fork, resume, archive and export every Claude Code session across
  every project — plus usage analytics, per-project launch control and quality-of-life
  features.
---

# Sessions & search

claudectl turns Claude Code's flat pile of chats into a browsable workspace: every project
Claude Code has ever opened, every session inside it, sorted by recency and searchable
across the lot.

## Session management

- **Session browser** — every Claude Code project and session, sorted by recency
- **Quick-resume** — ★/☆ shortcuts on the main screen jump straight back into recent sessions across all projects
- **Search** — type to filter sessions live; **🔍 Search all sessions** finds and resumes any session across every project
- **Transcript viewer & export** — read any session in a pager (`v`) with full-text search inside the conversation (`/`, `n`/`p` to jump between matches) and a message-position counter; export to markdown (`e`)
- **Session info** — per-session tokens, est. cost, models, git branch, duration (`i`)
- **Archive** — move sessions to a restorable `archived/` folder instead of deleting (`d`, toggle view with `A`). The archived view spans **every configured account**, like the live list, and Restore puts a session back in the account it came from.
- **Rename / Fork / Continue** — rename (`r`), fork (`f`), or continue the latest session (`claude -c`)
- **Tags** — tag sessions (`t`); tags show inline and are searchable
- **Changed files** — list the files a session edited/created, derived from its tool calls (`F`)

- **Context hand-off** — start a *new* session seeded with any prior session's transcript, from any account: **Hand off** on the session row in the GUI, `⇧K` in the terminal UI. See [Context hand-off](context-handoff.md).

The full key map for these actions is on the [Usage](tui.md#key-bindings) page.

## Usage analytics

- **Usage stats dashboard** — tokens (in/out/cache) and estimated cost per project and per session, parsed from local transcripts; cached for instant reopening
- **Plan usage** — daily/weekly limit bars with reset times shown on the main screen
- **Daily token tracking** (⚙ Usage stats → `d`) — per-day table of the last 14 days: tokens in/out/cache, est. cost, sessions, bar chart, today highlighted, live plan-window % alongside. Optional `daily_token_alert` badge on the main screen when today's tokens cross your threshold.

Costs are estimates at published API rates — useful as a value/consumption gauge if you're
on a subscription plan.

## Per-project launch control

- **Effort / model / permissions / agent** — reasoning effort, model override, `--permission-mode`, and `--agent` before each launch; effort/model/permission remembered per project
- **New-session options** — name the session (`-n`) and launch in a git worktree (`-w`)
- **Extra PATH entries** / **Add directories** — per-project PATH dirs and `--add-dir` context roots

## Quality of life

- **Themes (17)** — switch palette in Settings (live preview, cursor stays on the selection): default, ocean, forest, mono, ember (red), plus Catppuccin Mocha, Catppuccin Latte, Tokyo Night, Dracula, Nord, Gruvbox, Rosé Pine, Kanagawa, Everforest, Ayu, Monokai Pro, Solarized
- **AI session titles** — unnamed sessions show their AI-generated transcript title
- **Settings screen** (⚙) — editor, claude.exe path, **config dir / account** (`CLAUDE_CONFIG_DIR`), theme, and default launch options (`~/.claude/claudectl.json`)
- **Live model list** — the launch picker reads what Anthropic actually offers (using Claude Code's own login, no API key, refreshed daily in the background), so a newly released model is selectable without updating claudectl. Cost/capability/"best for" stay curated and are inherited by family. Offline or logged out it falls back to the bundled list — never to an empty picker — and a retired model you had pinned keeps its place with a warning rather than resetting itself to *default*.
- **Self-update** — claudectl checks PyPI for a newer release and says so in the banner. `⚙ Updates` (and the GUI's Plugins page) offers the upgrade; `Settings → Updates` switches between *tell me*, *install on quit* and *off*. The install runs in its own window once claudectl exits — pip cannot rewrite the console script it is running from — and a git checkout is told to `git pull` instead of having a release installed over it.
- **Confirm dialogs & multi-select** — modern yes/no and checkbox pickers throughout; command keys accent-colored on every screen
- **Help screen** — press `?` for a keyboard reference, generated from the same table that drives the `/` action palette and the on-screen hints, so it cannot fall behind the keys
