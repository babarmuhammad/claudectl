---
description: >-
  Start a fresh Claude Code session seeded with a previous session's transcript — including
  one that belongs to a different account. The way to carry work forward when the context
  window fills up or an account hits its limit.
---

# Context hand-off between sessions

**Hand off** on any session row in the [desktop GUI](desktop.md), or `⇧K` in the sessions
menu of the terminal UI.

Start a **new** session that already knows what the previous one was doing — and launch it
under **any account you like**, not necessarily the one that produced the transcript.

## Why you want it

Two situations end a productive session for reasons that have nothing to do with the work:

- **The context window filled up.** Once `/compact` has run, the details are gone. A fresh session with the prior transcript on disk starts clean *and* informed, instead of clean and amnesiac.
- **The account hit its 5-hour or weekly limit.** With a [second account](accounts.md) configured, you pick the old session as the source, pick the other account as the target, and carry on immediately — same project, same context, different quota.

The same flow also covers "that exploration went well, start a real session from it" and
"pick up yesterday's thread without re-explaining it".

## How it works

1. **Pick the source session.** In the GUI the row *is* the source, so there is nothing to pick: press **Hand off** on the session you want to carry forward. The sessions list already spans **every configured account**, each row labelled with the one it belongs to — sessions from accounts other than the current one are there on purpose, and that is the whole point. In the terminal UI, `⇧K` opens a picker over the same set, newest first (`[work] Refactor the parser (2h ago)`).
2. **Pick the target account.** Which account the *new* session launches under. Defaults to the project's current account, and only asks at all when more than one account is configured.
3. **claudectl writes the transcript to disk** — `<project>/.claudectl/injected-context.md`, headed with the source session's title and the account it came from, followed by the whole conversation as `### User` / `### Assistant` sections.
4. **The new session launches with a pointer, not a paste.** It gets a short system-prompt line saying where the file is and to read it first for background. The full text is never put on the command line — a long transcript would blow past the Windows argv limit — so the model reads the file with its own tools, exactly the way [Plan → Execute](plan-execute.md) hands over a plan.

The new session inherits the project's usual launch setup, read from the **target**
account's project folder: default model, permission mode, `--add-dir` context roots, extra
PATH entries, and the project's own `system-prompt.txt` (merged with the pointer, not
replaced).

## Notes

- The context file is rewritten on each hand-off, so only the most recent injection is on disk.
- `.claudectl/` is machine-local — add it to `.gitignore` if the project does not already ignore it.
- The account you *launch under* is set with `CLAUDE_CONFIG_DIR`, the same mechanism the rest of [multiple accounts](accounts.md) uses. Usage from the new session counts against the target account.
- This is a hand-off, not a merge: the new session reads one prior transcript. For "what have the last few sessions been doing", that is [recent-work memory](memory.md#recent-work-memory); for "what does this project know", that is [project memory](memory.md).
