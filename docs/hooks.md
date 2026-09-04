---
description: >-
  claudectl's hooks manager — 31 ready-made Claude Code hook templates (formatting, safety
  guardrails, failure logging, notifications, memory freshness, context injection, token
  savers), AI-generated hooks, and repair of broken ones.
---

# Hooks

- **31 ready-made templates** — one-key install, toggle, or remove (edits `settings.json` safely), in seven families:
    - **Formatting** — Prettier, Ruff, ESLint and gofmt after every edit.
    - **Safety guardrails** that **block** dangerous tools (`rm -rf`, `git reset --hard`, force-push, sudo, curl; reading `.env`; writing secrets — exit-code-2 blocks).
    - **Failure logging** — every Bash command, every tool failure, every turn that ended in failure, and every denied permission (so the allowlist and auto-mode proposals have something to learn from).
    - **Notifications** — beep when a turn finishes, when Claude needs your input, when a subagent finishes, when an agent teammate goes idle, or when something changes `settings.json` under you.
    - **Memory freshness** — mark the files Claude edits stale so [auto-refresh](memory.md) re-extracts just those, re-evaluate on a directory change, re-inject memory right after `/compact` discards the context, record what context actually loaded (pairs with the [context weight audit](usage.md)), and record the session on exit so auto-memory has it to learn from.
    - **Context injection** — git status at session start; a compact **code-minimization** rule that curbs over-engineering (inspired by [Ponytail](https://github.com/DietrichGebert/ponytail)); and `suggest-subagent`, which names the project subagent that fits your prompt by keyword match, with no model call.
    - **Token savers** — `concise-output` trims narration and re-printed code; `filter-test-output` pipes test runs through a failures-only filter before the output enters context.

    Plus `run-tests-on-stop`, which runs pytest when Claude finishes a turn. Guards/blocks run as bundled Python (shell-agnostic); formatters no-op when the tool is absent.
- **AI-generate a hook** — describe what you want in plain language; Claude returns a validated hook spec (event + matcher + command) you preview and confirm before it's saved.
- **Remove broken/legacy hooks** — one action purges hook commands that error under a bash hook shell.

Hooks are written into Claude Code's own `settings.json`, read-modify-write and atomically,
so your existing hooks, permissions and output style survive every edit. With multiple
[accounts](accounts.md) configured, `claudectl sync-accounts` places the same hooks in every
account's config dir.

SessionStart hook injections are counted by the
[context weight audit](usage.md) — a hook that injects on every session is a
per-turn cost like any other.

!!! note "The plugin ships no hooks, on purpose"

    claudectl's hook manager already places the recall, worklog and guard hooks per account.
    Bundling the same hooks in the [Claude Code plugin](plugin.md)
    would give one `settings.json` entry two owners: installing both runs the recall hook
    twice on every prompt, and uninstalling either leaves the other behind looking broken.
