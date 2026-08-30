---
description: >-
  The Claude Code status line claudectl renders, the local failover proxy that retries a
  dead model instead of hanging, and the read-only view of Claude Code's checkpoint store.
---

# Status line, failover & checkpoints

## Status line (`claudectl statusline`)

Renders the Claude Code status line: model, cwd, git branch and worktree, context pressure,
and the 5-hour / 7-day rate-limit windows. Install it from ⚙ Settings, or point
`statusLine` in `settings.json` at:

```
"<python>" -m claude_sessions statusline
```

It runs on **every** conversation turn, so it is built to be cheap: the subcommand is
dispatched before the TUI or the usage poller is imported, the branch is read straight from
`.git/HEAD`, and repo state comes from a disk cache that never spawns git. The rate-limit
and context numbers come from the payload Claude Code already sends — no network call is
ever made.

## Model failover

Retry a dead model instead of hanging. ⚙ Settings → Failover.

Claude Code sends every turn as a fresh request and, when one fails, retries the *same*
request against the *same* model with backoff. So a model deregistered upstream, or a tool
schema the backing provider rejects, makes a session look frozen forever — nothing ever
tries a different model, because Claude Code has no such concept.

claudectl's failover proxy sits between `claude.exe` and the
[configured provider](providers.md) upstream. It forwards bytes
verbatim and, when a turn errors **before any response body byte has reached the client**,
rewrites the request's `model` and tries the next candidate. Request-level retry *is*
per-turn failover, because every turn is its own request. The routing log is the point — the
original complaint was not "a model died", it was "I could not see that a model died" — so
it runs in its own console window unless you hide it.

Configure the fallback list, port and log visibility in ⚙ Settings → Failover (GUI:
Settings → Failover), or drive it directly:

```
claudectl --failover-serve [port]   # run the proxy in the foreground
claudectl --failover-stop           # terminate the daemon named in the lock file
```

It runs as a detached child so closing claudectl does not leave every live session with
connection-refused, binds `127.0.0.1` only, and requires the configured OmniRoute key —
claudectl hands that to the session as `ANTHROPIC_AUTH_TOKEN`, so no extra setup is needed.
Requests carrying browser fetch metadata are refused outright: the proxy spends your
upstream quota, so a web page must not be able to reach it.

## Checkpoints

Sessions menu. Read-only view of Claude Code's own file-history store: the whole-file
snapshots it takes before edits, paired with the files the session actually touched. The
store is undocumented, so claudectl never decodes the snapshot names — it hashes the paths
the session edited and looks those up, which means a change to the scheme surfaces as
"cannot read the store" rather than as filenames paired at random. Restoring is left to
Claude Code's own `/rewind`; claudectl only reads.
