---
title: Running multiple Claude accounts without losing your place
description: Claude Code picks its account from CLAUDE_CONFIG_DIR. Here is how to run two or more side by side without losing sessions, memory or usage history.
date: 2026-09-01
tags: [claude-code, accounts, workflow]
author: Babar Muhammad Anas
faq:
  - q: How do I switch between two Claude Code accounts?
    a: Claude Code selects its account from the `CLAUDE_CONFIG_DIR` environment variable, which points at the config directory holding that account's credentials, settings and project folders. Set it to a different directory and run `claude` to log in there, and you have a second account; set it per-launch rather than globally and you can run both at the same time in different terminals.
  - q: Can I run two Claude accounts at the same time?
    a: Yes. Because the account is chosen by an environment variable read at process start, two sessions launched with different `CLAUDE_CONFIG_DIR` values run concurrently and independently. What breaks is everything around them — session history, project memory and usage totals all split along the same line unless the tooling in front reads every config directory.
  - q: What happens to my work when a Claude account hits its rate limit?
    a: The session stops being usable, but the transcript is already on disk. With a second account configured you can start a fresh session under the other account seeded with the previous session's transcript, so you continue the same work against a different quota. claudectl calls this a hand-off — a button on the session row in the desktop app, `⇧K` in the terminal UI — writing the transcript to `.claudectl/injected-context.md` and passing the new session a pointer to it.
  - q: Does project memory work across multiple Claude accounts?
    a: It should, and it depends on where the memory lives. claudectl stores the memory graph under the project's real working directory rather than under a config directory, so every account sees the same graph, and the features that feed it — lesson extraction, session topics, usage stats, freshness counts — read every configured account's sessions.
  - q: Why does my status line or hook only work on one account?
    a: Almost always because something resolved the config directory once at import or install time and cached it. A path derived from mutable state is a cache with no invalidation; it must be recomputed per call. Check that the hook is actually present in each account's `settings.json` rather than trusting a UI that reports "installed".
---

## The short answer

Claude Code decides which account it is using from one environment variable: `CLAUDE_CONFIG_DIR`, the directory holding that account's credentials, `settings.json`, `CLAUDE.md` and `projects/` folder. Point it somewhere else and run `claude` to log in, and you have a second account; set it per-launch instead of globally and both accounts run at the same time. The variable is the easy part. What breaks is everything built around it — your session history, project memory, hooks and usage totals all split along the same line, and most tooling silently reports only whichever account was active when it started up.

## The mechanism

```powershell
# account one — the default
%USERPROFILE%\.claude\

# account two
$env:CLAUDE_CONFIG_DIR = "$env:USERPROFILE\.claude-work"
claude   # /login here creates the second account's credentials
```

Everything account-shaped lives under that directory: credentials, `settings.json` (which is where hooks, permissions, `statusLine` and `outputStyle` live), the account-wide `CLAUDE.md`, and `projects/<encoded-path>/` with every session transcript for that account.

That has a consequence worth stating plainly, because it surprises people: **the same project opened under two accounts has two separate session folders**. Nothing is lost, but nothing is joined either. Your history for `D:\Projects\my-app` is in two places, and `/resume` will only ever show you one of them.

## What claudectl does with that

The tooling problem is not switching — it is not noticing that you switched.

**Named accounts.** ⚙ Accounts: add an account with a name and a config dir (claudectl creates the directory and can open `/login` immediately), rename it, switch the active one, or **open it in a new terminal with one key** so both accounts are running at the same time.

**Per-launch account.** The launch-options screen has an **Account** field. Pick which account *this* session starts under without changing your default. The field only appears when more than one account exists.

**One row per project, not per account.** If the same folder has sessions under two accounts, the project list shows a single row — default account primary, tagged `[+work]` — instead of a duplicate. Opening it merges every account's sessions into one list, with foreign-account sessions marked inline (`[work]`). Rename, archive, delete, fork and view all act on that session's own account, and resuming one launches under the right account automatically. That last detail is what makes the merge safe rather than confusing: the list is unified, the actions are not.

**One usage bar per account.** The plan-usage banner shows a bar row per account, labelled by email or name, with each account's session and weekly windows and their reset times. A single account stays a single compact bar.

**Account-accurate memory.** The memory graph lives under the project's *real path*, shared by every account, and the features that feed it read **every** account's sessions: lesson extraction, the CLAUDE.md session-topics block, per-project usage stats, workspace freshness counts and the recent-sessions quick-resume list. A project used under two accounts is one merged row in the usage dashboard, not two.

**Levelling up.** `claudectl sync-accounts` places what you have actually provisioned — hooks, status line, settings — into every account's config dir. `--dry-run` first if you want to see it before it happens.

## The rate-limit workaround, properly

This is the case that makes multi-account worth the setup. An account hits its 5-hour or weekly window mid-task. The work is not lost — the transcript is already on disk — but the session is over.

**Hand off** on the session row in the desktop app, or `⇧K` in the sessions menu of the terminal UI:

1. **Pick the source session.** In the desktop app the row *is* the source — the sessions list already spans *every* configured account, each row labelled with its own: `[work] Refactor the parser (2h ago)`. Foreign-account sessions are in that list on purpose; that is the entire point. In the terminal UI, `⇧K` opens a picker over the same set, newest first.
2. **Pick the target account.** Which account the *new* session launches under. Defaults to the project's current account, and only asks at all when more than one is configured.
3. **claudectl writes the transcript to disk** — `<project>/.claudectl/injected-context.md`, headed with the source session's title and originating account, then the whole conversation as `### User` / `### Assistant` sections.
4. **The new session launches with a pointer, not a paste.** It gets a short system-prompt line saying where the file is and to read it first. The transcript never goes on the command line — a long one would blow past the Windows argv limit — so the model reads the file with its own tools.

The new session inherits the project's usual launch setup read from the **target** account's project folder: default model, permission mode, `--add-dir` roots, extra PATH entries, and that project's `system-prompt.txt`, merged with the pointer rather than replacing it.

The same flow covers two other situations: "the context window filled and `/compact` ate the details", and "that exploration went well, start a real session from it".

Notes worth knowing: the context file is rewritten each hand-off, so only the most recent injection is on disk; `.claudectl/` is machine-local and belongs in `.gitignore`; and usage from the new session counts against the target account, which is the whole idea.

## The bug class this feature attracts

Multi-account support fails in one characteristic way, and it is worth describing because it will bite anyone writing their own scripts around `CLAUDE_CONFIG_DIR`.

All three of the following were found chasing a single report — "the status line only works on the default account" — and all three are the same mistake in different costumes.

**A module-level path constant.** A line like

```python
settings_path = os.path.join(config_dir, 'settings.json')   # evaluated at import
```

means every reader and writer of that file can only ever touch the account that was active when the module was imported. With three accounts configured, two of them had no status line and **no hooks at all**, while every surface confidently reported "installed". The fix is a function — `settings_path_for(cfgdir)` — plus load and save that take the directory as an argument.

**Wrong precedence.** The config-directory resolver checked a saved setting and a default, and never looked at `CLAUDE_CONFIG_DIR` at all. But claudectl itself *writes* that variable at five different spawn sites to choose the account — so a status line running inside a session launched under account B resolved claudectl's saved setting and labelled every session with account A. Precedence must be **environment > setting > default**, which is also Claude Code's own order.

**Frozen colours.** `from .config import C_WARN as _WARN` freezes a value at import; the theme system rebinds `config.C_*` later and the frozen copy never moves. Harmless-looking, and it made a test pass alone and fail in a full run depending on collection order.

The general rule underneath all three: **a module-level constant derived from mutable state is a cache with no invalidation. Derive it in a function.**

There is a corollary for anyone running a test suite from inside a Claude Code session: `CLAUDE_CONFIG_DIR` is set in that environment, so tests that resolve the config directory pick up your live account. claudectl's `conftest.py` pops the variable at *module* level rather than in a fixture, because a fixture runs too late for anything captured at import.

## One security note

If you build a local API around this — claudectl's GUI is one — the config directory becomes a parameter, and it is joined with `projects` and a project name across dozens of endpoints. That makes it a filesystem read primitive. It has to be validated against the set of accounts the tool actually knows about, not accepted as an arbitrary path. A missing or unknown value should be a `400`, not a `500` and not a directory walk.

## When it is not worth it

Two accounts is real overhead: two logins, two `settings.json` files to keep level, two sets of hooks. If you never hit a rate limit and never need to separate client work from personal work, one account and a good session archive is the simpler system. The setup earns its keep at the moment a five-hour window closes on you mid-task and the answer is one keypress instead of an afternoon.

Reference detail: [docs.claudectl.space/accounts](https://docs.claudectl.space/accounts/) and [the context hand-off page](https://docs.claudectl.space/context-handoff/).
