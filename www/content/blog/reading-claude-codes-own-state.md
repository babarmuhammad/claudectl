---
title: "Reading Claude Code's own state: sessions, plugins, checkpoints, settings"
description: Claude Code keeps transcripts, checkpoints, plugin caches and settings on disk in formats it never documented. How to read them without breaking them.
date: 2026-09-01
tags: [claude-code, internals, tooling]
author: Babar Muhammad Anas
faq:
  - q: What files does Claude Code keep on disk?
    a: Under the config directory it keeps `projects/<encoded-path>/<session-id>.jsonl` transcripts, `settings.json` for hooks, permissions, output style and the status line, an account-level `CLAUDE.md`, `file-history/<session-id>/` snapshots taken before edits, plugin caches for marketplaces and installs, and `.claude.json` with per-project metadata. Most of these formats are not documented and have changed at least once.
  - q: Where are Claude Code's checkpoints stored?
    a: In `~/.claude/file-history/<session-id>/`, as whole-file snapshots named `<name>@v<n>`. The `<name>` part is the first 16 hex characters of the SHA-256 of the file's absolute path exactly as the tool recorded it, so the safe way to read the store is to hash the paths a session edited and look those up, never to try to decode a snapshot name back into a filename.
  - q: Is it safe to edit Claude Code's settings.json from a script?
    a: Only with a read-modify-write and an atomic replace. The file holds your hooks, permissions, output style and status line together, so a script that rewrites it wholesale erases whatever it did not know about, and a plain write that dies partway leaves truncated JSON that breaks Claude Code itself — not just your script.
  - q: Why does my tool report the wrong Claude Code project age?
    a: Most likely because `.claude.json` stores timestamps in epoch milliseconds and the code read them as seconds. Dividing by the wrong factor makes every age come out hugely negative, which renders as "just now" and looks entirely plausible, so it survives review. Test with a timestamp that is definitely old rather than one generated during the test.
  - q: How can I tell how much context a Claude Code session has used?
    a: Claude Code sends the number to the status line in its JSON payload as `context_window.used_percentage`, along with `rate_limits.five_hour` and `rate_limits.seven_day`. Read those fields from the payload rather than making a network call — and verify field names against the payload you actually receive, since a plausible-looking wrong name renders nothing and fails silently.
---

## The short answer

Claude Code keeps a lot on disk — session transcripts, `settings.json`, checkpoint snapshots, plugin caches, per-project metadata — and almost none of the formats are documented. That makes them fair game to *read* and dangerous to build on. The discipline that works is narrow: read the state, but never depend on the naming scheme; re-derive keys from data you already have instead of decoding them; treat any file another program also writes as read-modify-write with an atomic replace; and when a store stops matching your expectations, report that you cannot read it rather than guessing. Every rule below came from getting one of them wrong first.

## Transcripts

```
<config-dir>/projects/<encoded-project-path>/<session-id>.jsonl
```

One JSON object per line: user turns, assistant turns, tool calls, tool results. These are the richest source of truth on the machine — they carry the model used, token counts, the working directory, and every file the session touched.

Two rules.

**Stream them.** They get large; a 2,787-message session is a normal week's work and files past 100 MB exist. `readlines()` on one of those is a memory event, not a read. One reader for the format, and the reader streams — with `limit`/`offset` over yielded objects, a `max_bytes` cap, and a substring prefilter tested against the *raw line* so a caller that only wants the `"Bash"` entries never pays for a `json.loads` it discards.

**Do not decode the folder name.** The encoding is lossy: `D:\Projects\my-app` becomes `D--Projects-my-app`, and `\\server\share\Project` becomes `--server-share-Project`, whose leading dashes defeat any attempt to recover a drive letter. Every transcript line already carries the real `cwd`. Read it. That is exact for every encoding and cheaper than the directory walk it replaces. (More on the Windows specifics in [managing Claude Code sessions on Windows](/blog/managing-claude-code-sessions-on-windows).)

## `.claude.json`

Per-project metadata. Two details will burn you.

**Keys are real paths with forward slashes, even on Windows.** Not the encoded folder name, not backslashes.

**Every timestamp is epoch milliseconds.** Read as seconds, the age comes out hugely negative — and a hugely negative age renders as "just now", which looks completely plausible. That is what makes it survive code review: the bug's output is indistinguishable from correct output for recent projects. The only test that catches it uses a stamp that is *definitely old* and asserts the age is large, rather than generating a timestamp inside the test.

## Checkpoints

Claude Code takes whole-file snapshots before it edits, in:

```
~/.claude/file-history/<session-id>/<name>@v<n>
```

The store is undocumented. `<name>` is `sha256(<absolute path exactly as the tool recorded it>)[:16]` — established empirically by hashing a real transcript's paths against a real directory and checking the match: 6 of 6 matched, 0 left over.

Knowing that, the tempting move is to decode: walk the snapshot directory and reverse the names into filenames. You cannot — SHA-256 does not run backwards — and any heuristic pairing is a guess presented as a fact.

The correct move is the inverse. **Hash the paths this session edited** (they are in the transcript) and look those hashes up in the store. The property that buys you is graceful failure: if Anthropic changes the scheme, nothing matches, and the tool reports `recognised: False` — "cannot read the store" — instead of confidently pairing snapshots with filenames at random.

Read-only, too. Restoring is `/rewind`'s job; claudectl enforces this with a test that greps the module for any write call, because "we only read it" is a claim that decays the moment someone adds a convenience feature.

## Plugin caches

```
<config-dir>/plugins/known_marketplaces.json
<config-dir>/plugins/installed_plugins.json
```

Readable, and worth reading — showing which plugins are installed and which marketplaces are known is genuinely useful. But **the format has already changed once**, which is the entire argument for the policy: every *mutation* shells out to the `claude` CLI rather than editing the JSON.

That is the general shape of the rule. Reading an undocumented file costs you a stale display when it changes. Writing one costs you a corrupted install.

## `settings.json`

The most consequential file, because it is not yours. Hooks, permissions, `outputStyle` and `statusLine` all live in it together, and it is Claude Code's own file.

**Read-modify-write, always.** A script that writes the whole file erases whatever it did not know about. If you are adding a hook, load the current contents, insert, write back.

**Atomically.** A plain `open(path, 'w')` that dies partway leaves truncated JSON and breaks the user's entire Claude Code session, not just your tool. Temp file plus `os.replace`:

```python
def write_json_atomic(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)   # atomic on the same filesystem
```

claudectl had three modules that had each solved this locally before it was consolidated into one helper with eleven call sites and an AST-walking test that fails when any writer near anything named "settings" uses a plain `open(..., 'w')` — because five of them were written the plain way one at a time, and a sixth would have been.

**A missing file is an empty state; a file that will not parse is a fault.** Seven modules in this codebase had written `except Exception: return {}` around a `json.load`, and every one of those readers was also a writer — so the first truncated write was also the last time the data existed. The one that mattered most read Claude Code's `settings.json`: returning `{}` for an unparseable file erased the user's hooks, permissions and output style on the next save. The fix is to move a corrupt file aside as `<name>.corrupt-<timestamp>` and report it.

**Removing a key is sometimes the correct write.** Setting `outputStyle` to `'default'` is done by *deleting* the key, not writing the literal — pinning the literal freezes behaviour against a future change in what "default" means.

## Status line payloads

If you write a status line, Claude Code hands you a JSON payload on stdin on **every turn**. It already contains more than most implementations use:

| Field | What it is |
|---|---|
| `context_window.used_percentage` | How full the context window is |
| `rate_limits.five_hour` | The 5-hour window |
| `rate_limits.seven_day` | The 7-day window |

There is a lesson attached to that table. claudectl's status line originally read `context_used_pct`, then `contextUsedPercent`, then `context.used` / `context.total`. Claude Code has never sent any of them. The segment was dead in production for its entire life — and its test passed, because the test asserted the same invented shape. It was testing the bug rather than the behaviour.

**Check field names against the payload you actually receive.** A plausible wrong name renders nothing, and nothing looks like "no data yet". The regression test now asserts the dead names render *nothing*, which is the only assertion that would have caught it.

The rate-limit fields are the second half of the same discovery: they were already in the payload while a background OAuth poll was being paid for to fetch the same numbers.

Because this runs per turn, cost matters. claudectl dispatches the `statusline` subcommand *before* importing its main module, which drags in `urllib`/`ssl`/`http.client` for a poll the status line must never make — that alone took the round trip from about 125ms to 58ms. The git branch is read straight from `.git/HEAD`, which is cheaper than a cache lookup and can never be stale; everything else comes from a disk cache that is guaranteed never to spawn a subprocess.

## When the docs and the disk disagree

The sharpest example. Anthropic's documentation for agent teams says a team is named `session-` plus the first 8 characters of the session id. On the machine this was tested on, `teams/` is **empty** and `tasks/` is keyed by the **full session UUID**.

Docs and disk already disagree, so the only defensible behaviour is to report a task directory as itself and never join it to a team by a rule the data does not support. Same discipline as the checkpoint store: when the shape you expected is not there, say so.

This is the principle that ties all of the above together, and it is worth stating as a rule you can apply to any undocumented store:

> Read it. Do not build on its naming. Re-derive keys from data you already hold. Write only through the tool that owns the format, and only atomically. When it stops matching, report unreadable — never guess.

## One more, for your own files

If your tool has its own settings file, `load_settings` must carry the keys it does not recognise. Save writes back whatever load returned, so filtering unknown keys on read means an older version of your tool **silently erases** a newer one's settings — and syncing a config file between two machines is enough to trigger it.

Reference: [docs.claudectl.space/reference](https://docs.claudectl.space/reference/) for the per-project file layout and session encoding, [docs.claudectl.space/statusline](https://docs.claudectl.space/statusline/) for the status line and checkpoint viewer.
