---
description: >-
  Run two or more Claude accounts side by side — named config dirs, per-launch account
  selection, merged project rows, cross-account context injection and account-accurate
  memory.
---

# Multiple Claude accounts

⚙ Accounts. Run two (or more) accounts with almost no friction — claudectl owns the config
dir (`CLAUDE_CONFIG_DIR`), which is what decides the account:

- **Named accounts** — add an account (name + config dir; claudectl creates it and can open `/login` right away), rename it, switch the active one, or **open it in a new terminal with one key** so both accounts run **at the same time**.
- **Per-launch account** — the launch-options screen has an **Account** field: pick which account this specific session starts under, without changing your default.
- **All accounts in the usage bar** — the plan-usage banner shows **one bar per account** (labeled by email/name) and updates dynamically, so you see every account's session/weekly limits at a glance. A single account stays a single compact bar.
- **One row per project, not per account** — if the same folder has sessions under two accounts, the project list shows a single row (default account primary, tagged `[+other-account]`) instead of a duplicate. Opening it merges every account's sessions into one list, foreign-account sessions marked inline (`[account-name]`); rename/archive/delete/fork/view all act on that session's own account, and resuming one launches under the right account automatically.
- **Hand off across accounts** (**Hand off** on a session row in the GUI, `⇧K` in the terminal UI) — start a new session seeded with the transcript of any prior session for this project, including ones from a different account. This is how you keep working when one account hits its limit: pick the session you were in, pick the other account, carry on. See [Context hand-off](context-handoff.md).
- **claudectl's own calls move too** — when the active account's window is full and claudectl wants to generate an agent, a skill, a CLAUDE.md or a memory cycle, it stops and offers the accounts that still have headroom rather than launching a call it knows will fail. Unattended work skips and records the reason instead of prompting. See [Rate limits and a second account](tui.md#rate-limits-and-a-second-account).
- **Account-accurate memory** — the memory graph lives under the project's real path (shared by every account), and the features that feed it now read **every** account's sessions: lesson extraction, the CLAUDE.md session-topics block, per-project usage stats, workspace freshness counts, and the recent-sessions quick-resume list. A project used under two accounts is one merged row in the usage dashboard, not two.

`claudectl sync-accounts` levels every account up to what you have actually provisioned —
hooks, status line and settings placed once, applied everywhere.
