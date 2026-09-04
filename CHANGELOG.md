# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.9.0] - 2026-09-04

Two things claudectl says about itself turned out not to be true, and both are
now. It said a GUI job would never hang on a keyboard, and three of them did.
It said the local API was guarded by three layers, and it had two — while the
page carrying the key was handed to anyone who asked for it. This release is
mostly that: the security sweep, the hangs, and a session list you can read.

### Security

- **Cross-site scripting in the desktop GUI, and it reached a shell.** Every
  value that reached an inline event handler in `web/app.js` — seventy-nine
  interpolations — was escaped at the wrong layer. `${JSON.stringify(v)}` inside
  `onclick='…'` escapes for JavaScript and *nothing* for HTML; `'${jsq(v)}'`
  inside `on…="…"` did the mirror image. One apostrophe closed the attribute and everything after it was
  parsed as new attributes.

  The values are not yours: a filename from a repository you cloned, an MCP
  server name out of a project's `.mcp.json`, a git branch, a skill directory,
  a plugin name. Injected script ran same-origin with the API token in scope,
  and `/api/settings` plus `/api/open-editor` turn that into an arbitrary
  command. One helper, `hesc()`, applies both layers in the right order and is
  now the only way a value reaches a handler; `tests/test_inline_handlers.py`
  fails the build if a bare `JSON.stringify` ever appears in markup again.

  It was also a plain bug: a suggestion containing "don't" broke the page.
- **The page that carries the API token gave it away.** `GET /` was served on the
  `Host` check alone, and `/` is the response the per-run token is substituted
  into — so any process that could open a socket to the port could simply ask
  for it, which on Windows includes a process running as a *different user*,
  because loopback is not a user-identity boundary. `/` now takes the token in
  its query string exactly as `/graph` always has, the launcher puts it there,
  and the SPA drops it from the address bar on boot.
- **The guard now has the three layers it always claimed.** `gui._guard()`
  implemented `Host` and the token; the documented middle layer — rejecting a
  cross-site fetch — existed only in the failover proxy. It is the layer that
  still holds if the token leaks, so it is the one that mattered here. It had to
  be written as an allowlist rather than the proxy's outright rejection, because
  the SPA's own `fetch()` sends those headers.
- **`git clone` ran an attacker's URL before anything reviewed it.**
  Installing a skill from git passed the URL straight to `git clone` with no
  scheme check and no `--`, and `ext::sh -c …` is a real git transport that
  executes on clone. The review gate that shows you what will be installed runs
  *after* the checkout, so it never protected the fetch. Remote URLs are now
  validated in one place (`proc.remote_url_ok`) and the option list is
  terminated; the same applies to adding a plugin marketplace and merging a
  worktree branch.
- **Two endpoints deleted whatever they were told to.** `/api/agents/delete`
  reached `os.remove(body['file'])` and `/api/skills/remove` reached
  `shutil.rmtree(body['dir'])` with no validation at all, so
  `{"dir": "C:\\Users\\you"}` was a recursive delete of your home directory.
  Both now require a path claudectl actually manages. `target_cfgdir`, which
  becomes `CLAUDE_CONFIG_DIR` for a spawned `claude`, is validated by the same
  account allowlist as `cfgdir` — it was skipped only because it is spelled
  differently.
- **Path traversal in output styles.** `read` and `delete` joined the style name
  into a path raw, three functions away from a `save` that had always sanitised
  it: `?name=../../../../Users/you/Documents/notes` read that file, and delete
  removed any `.md` on the volume.
- **A repository name could inject script into the architecture graph**, which is
  served same-origin with the GUI and under the same policy. The graph's JSON
  payload is also now safe against U+2028/U+2029, which are JavaScript line
  terminators that `ensure_ascii=False` emitted raw.
- **`.claudectl/` marks itself never-commit.** claudectl writes it into *your*
  repositories, and it holds `bash-log.txt` — every Bash command Claude Code ran,
  which routinely includes `export TOKEN=…` and `curl -H "Authorization: …"` —
  plus `injected-context.md`, an entire transcript. It now seeds a `.gitignore`
  the first time it is created. `claudectl.json`, which holds your OmniRoute key,
  is written `0600`.
- **`/api/state` echoed the OTEL headers value back to the page**, and that
  setting is documented as carrying `Authorization=Bearer <token>`. It is the one
  secret in the settings payload that never got `omniroute_api_key`'s write-only
  treatment. It is reported as a boolean now, and the field in the settings page
  is write-only: blank means keep what is stored.
- A non-ASCII byte in the `X-Claudectl` header raised inside `hmac.compare_digest`
  and printed a traceback instead of answering 403.

### Added

- **Logs, in both interfaces.** A new **⚙ Logs** screen in the TUI and **Logs**
  page in the GUI, over one append-only file at
  `~/.claude/claudectl-events.jsonl`: claudectl's own headless Claude calls and
  why they failed, background job crashes, auto-memory scheduler passes, the
  failover proxy, and any state file it had to quarantine. Newest first,
  filterable by level and by text, capped at 256 KB.

  Until now those failures went nowhere at all. The `claudectl` logger carried a
  `NullHandler` unless `CLAUDECTL_DEBUG` was set — which is off for everyone —
  so around forty `log.exception` sites, including *every* background job crash
  and *every* faulted API handler, wrote to nothing. One logging handler now
  fans them all in; no per-turn path writes to the log, and no hook touches it.
- **Hand off** — on every session row in the GUI, and `⇧K` in the terminal UI.
  Start a new session seeded with a previous one's transcript, under any account
  you like. It replaces the Tools-tab "new chat with injected context" flow,
  where you had to pick the source session from a list; the row *is* the source
  now.
- **A `standard` skin** — no chassis, system font, hairline borders. The plain
  one, for when the point is the content. 32 palettes, 8 skins.
- **`headless_quota` setting** — `prompt` (default), `auto` or `off`. Decides
  what happens when claudectl wants to make one of its own Claude calls and the
  account's limit is already full.

### Fixed

- **A session row shows what the session is about again.** The action strip was
  invisible until you hovered a row, but it still occupied its full width at all
  times — so adding **Hand off** as a tenth action took roughly a quarter of the
  title away from every row, whether you were reading or acting. The strip is out
  of the layout entirely now: the title gets the whole row at rest, the actions
  fade in over its right end on hover, and nothing moves. They are reachable by
  keyboard for the first time. The row also carries the full title as a tooltip,
  the preview text kept behind it is no longer clipped to 65 characters, and an
  archived row shows its AI title instead of falling back to the preview.

  In the terminal UI the name column scales with the window instead of a fixed 30
  columns, and the hint bar drops whole hints rather than cutting the last one
  mid-word — which is what `⇧K hand off` had been doing at 80 columns.
- **Three GUI jobs hung until a six-hour reaper.** AI-generate agent, AI-generate
  hook and AI CLAUDE.md all reached an interactive terminal primitive on a
  background thread, where no keypress is ever coming: nothing logged, nothing
  timed out, and the interface said "running" the whole time. Each is now split
  into a non-interactive function the job calls and a thin terminal wrapper.

  The underlying cause is more general and is now closed generally: a module that
  did `from .ui import text_input` held its own reference, which patching `ui`
  never moved. The bridge sweeps for the original function instead of naming
  modules — naming them is what had already failed twice.
- **claudectl spent accounts that had nothing left.** Every internal
  `claude -p` call — AI agents, AI skills, MCP analysis, system prompts, AI
  CLAUDE.md, memory and lessons extraction, Plan → Execute and its council,
  scheduled loops — launched without checking the account's rate-limit window.
  On a full window the call failed and you were told "No output from Claude",
  while a second configured account sat there with headroom.

  claudectl now stops and offers the accounts that still have headroom: a picker
  in the TUI, the existing approval modal in the GUI. Unattended work (the
  scheduler, the detached scan worker, a scheduled loop) never prompts — it
  records the reason and skips, rather than quietly spending an account you did
  not offer. The check reads the usage data the plan-usage poller already
  fetched, so it costs no network call; an unknown limit is never treated as a
  full one. `claude mcp`, `claude plugin` and `claude --version` are untouched —
  they cost no quota.
- **The two foreground runners destroyed the reason a call failed.**
  `run_with_progress` and `run_with_progress_stdin` sent the child's stderr to
  `DEVNULL` and returned nothing on a nonzero exit, so a rate limit, an expired
  login and a genuine crash were all reported identically as "No output from
  Claude". They now capture stderr and latch it in the same place
  `_run_cancellable` already did, and the callers report what `claude` actually
  said.
- **Rate-limit detection no longer fires on the model's own output.** Reading the
  whole of stdout to find the real refusal meant a bare `429` matched a token
  count and a bare `quota` matched any answer that discussed rate limiting —
  locking an account out of headless work for fifteen minutes after a run that
  had nothing wrong with it.
- **Archived sessions from other accounts were invisible**, and the ones you
  could see could not be restored to the right place.
- **`/api/sessions` was re-parsing every transcript on every GUI restart** — an
  8-second median on a large project. The disk cache existed but nothing on the
  cold path could reach it. It also now carries a schema number, so a change to
  what claudectl stores about a session is visible on old sessions instead of
  only on new ones.
- **The window kept painting while it was not on screen.** CSS animations, a
  world's overlay and the CRT caret all kept running behind a hidden canvas, and
  a light or OLED palette flashed the wrong colour during the swap. Blur is
  debounced, focus is not, and the dashboard's teardown-and-refetch is debounced
  with it — Qt fires spurious blur/focus pairs on things that are not a focus
  change. A tab becoming visible inside the debounce window could also leave the
  page stuck in its blurred state.
- **Unresolvable projects cost 98% of the project listing.** A path that no
  longer exists on disk was re-walked on every call, forever, because only
  successful lookups were cached. `claude mcp list` is cached for 30 seconds now
  including its failures — which is the case that costs the most, because a
  slow-failing MCP server is exactly when it is slowest.
- **The whole OTEL section of the settings page was inert.** It read
  `ST.otel_enabled` and friends while `/api/state` nested them under `ST.otel`,
  so every field rendered its default no matter what was saved — and pressing
  Save then wrote those defaults back over the real configuration, including
  blanking the headers. The payload is flat now, named exactly as
  `/api/settings` takes the values back.
- **Two "Open in editor" buttons had never worked**, sending a parameter the
  endpoint does not read. `/api/job/<id>/decide` — the route every approval gate
  resolves through — wrote two HTTP responses for one request.
- **One noisy warning was crowding the event log out.** The dedupe key contained
  the measurement it was deduplicating, so 609 of 674 events were the same
  warning differing by one decimal. Only decimals collapse now: an HTTP status or
  a project path with a digit in it is part of the event's identity, and folding
  those was dropping real failures.

### Changed

- `.claudectl/` is created through one helper, and `config.write_atomic` takes an
  optional file mode.
- The plugin manifest, the marketplace entry, the docs page and `CITATION.cff`
  are all checked against `pyproject.toml` by one test.

## [1.8.2] - 2026-09-01

Memory was twelve things and the interface could name three of them. This
release makes the whole layer legible — what exists, what writes it, when it
reaches a session and what it costs — and fixes the four ways it was quietly
spending more of your limit than you asked it to.

### Fixed

- **Path-scoped rule files were loaded on every turn, not lazily.** They were
  written with `globs:` frontmatter — Cursor's key. Claude Code reads `paths:`,
  and a rule file without it is loaded unconditionally, so the entire point of
  splitting memory into one file per module was lost while the UI reported each
  one as free until Claude opened a matching path. Measured on this repo:
  **22 238 → 18 363 always-on tokens, 3 875 off every single turn.** Existing
  rule files are rewritten with the correct key on the next sync — free, no
  Claude call.
- **A failed memory cycle was reported as progress.** Failed and skipped units
  were summed into one `pending_units`, and every surface worded it "the next
  cycle takes them" — so on a rate-limited account six *failed* extractions an
  hour read as work safely queued. Failures are now counted as failures and
  shown with the error that caused them. A nonzero exit also discarded the real
  error text whenever no job was attached, which is exactly the scheduler and
  the detached background worker.
- **Auto-memory no longer speeds up when it has a backlog.** A capped cycle used
  to schedule the next pass 45 seconds later, again and again until the project
  caught up. Across several opted-in projects that is most of a daily limit
  inside an hour, and on a failing account it was the same dead calls in a loop.
  One pass runs when claudectl starts and one every interval you configured: the
  per-cycle cap decides how much a pass may spend, the interval decides how
  often that happens, and nothing else schedules work.
- **Opening a project no longer adds an unscheduled memory cycle.** Opting a
  project into background auto-memory also implied "refresh whenever you open
  it", so the configured interval bounded nothing — and the GUI never consulted
  the per-project flag at all, deciding from the global setting alone. Background
  auto-memory now owns the spend; **Build with Claude** still refreshes on demand.
- **Disabling or deleting a hook applies to every account.** Installing one
  already did. Turning one off wrote only the account you happened to be looking
  at, so a hook installed once and disabled once kept firing from the others —
  with the UI showing it as off. Selecting an account still acts on that one alone.
- **Session topics no longer list claudectl talking to itself.** `claude -p`
  leaves a transcript like any other session, so the CLAUDE.md SESSIONS block was
  spending always-on tokens describing claudectl extracting a module or
  distilling lessons.
- **Opening the recall preview no longer counts as reinforcement.** It appended
  to the hits log, so inspecting memory reshaped which facts survived eviction.
- **The reinforcement log says how many hits are waiting to be folded in**,
  instead of a fixed phrase derived from an unrelated number.
- **Lifetime memory spend includes lesson scans**, which are Claude calls like
  any other.
- **A broken `@import` in a CLAUDE.md is flagged.** Claude loads nothing for it
  and says nothing about it.

### Added

- **The Memory tab inventories every memory artifact** — the graph, the CLAUDE.md
  digest, the AUTOGEN and SESSIONS blocks, the path-scoped rule files, the
  worklog, both sidecars, the cross-project conventions block, the workspace
  manifest, lessons, version snapshots and the per-prompt injection — grouped by
  **when each one reaches a session**: always on, every prompt, when relevant, or
  stored and never loaded. Each row says what it does for you, what it costs and
  what to press. The always-on total is given as a share of the context window,
  which is the only denominator that makes a token count mean anything.
- **What a cycle spent and what it dropped.** Cost per cycle, cumulative spend
  for the project, the names eviction removed, the facts recall reinforces most
  (clickable, showing what each one actually is), and how many sessions each
  lesson is from being dropped.
- **The CLAUDE.md tab shows the file block by block** — your prose, KEEP-fenced
  regions, AUTOGEN, SESSIONS and the memory digest — each with its token cost and
  the one button that regenerates that block alone.
- **Workspace health is per-check rows**, each carrying the points it is worth
  and the button that clears it, instead of terminal output.
- **Version history sits beside the artifact it belongs to** — graph history on
  the Memory tab, CLAUDE.md history on the CLAUDE.md tab — with recent versions
  shown and the rest collapsed.
- **The recall preview says why each entity was picked.**
- **The next pass is stated wherever a cycle left work queued**, so "still
  queued" has an answer to "until when".

### Changed

- **The CLAUDE.md memory digest carries facts, not a table of contents.** It was
  spending its budget listing module names — which tells a session nothing `ls`
  would not — and now spends it on the highest-signal lessons and the module
  dependency edges.
- **The Audit tab is now only what a turn costs across every surface at once**,
  project-scoped and account-scoped together, with the version history it used to
  host moved beside the files it belongs to.
- Generated files (`.claude/rules/claudectl-mem-*.md`, `docs/api.md`,
  `plugin/skills/`) are marked `linguist-generated`, so their diffs collapse in
  review.

## [1.8.1] - 2026-08-31

### Fixed

- **Auto-memory crashed in the GUI**, with
  `Memory update failed: 'NoneType' object has no attribute 'reconfigure'`. The
  stale-on-edit work added in 1.8.0 imported a *hook script* for one path
  helper, and a hook reconfigures stdout when it is imported — correctly, since
  Claude Code hands it a pipe. The GUI runs as a windowed process with no
  console, where `sys.stdout` is `None`, so that line took down the whole
  refresh cycle. In the GUI auto-memory therefore never ran at all: the hourly
  pass failed silently on every project, and opening a project only made the
  same failure visible. The dependency now points hook → library, never the
  other way, and a gate walks the package for any module importing an entry
  point — it found two more instances immediately (see below).
- **The context-weight audit under-reported two always-on hooks.** It imported
  `minimalcode_hook` and `concise_hook` for their rule text inside a bare
  `except Exception: pass`, so in the GUI the import failed and both hooks'
  per-session cost silently vanished from the total. The text now lives in
  `hookrules.py`, which the audit and the hooks share.
- **A hook no longer dies when there is no stdout.** Setting UTF-8 on a pipe is
  right; being the reason the hook exits when stdout is absent is not.
- **Auto-memory runs on launch and on the interval, from either interface.**
  The periodic pass had one caller, in the GUI's server startup — so on the
  terminal side "keep this project's memory updated automatically" only ever
  happened when you opened the project. The scheduler loop itself had no test
  beyond "the stop flag can be set"; the launch pass, the repeat and the stop
  are now covered.

## [1.8.0] - 2026-08-31

### Added

- **`claudectl --help`** (and `-h`, `help`, `--version`, `-V`). A pip install used to
  answer the most obvious command by opening a full-screen terminal UI. The help text
  covers every subcommand, what the tool does and where its state lives; it is answered
  from `cli.py`, which imports the standard library and nothing else, so it costs nothing
  and cannot be broken by anything in the TUI stack. A test enumerates the dispatch table
  from the source, so the text cannot fall behind it.
- **Hide projects you never want to see.** Every folder Claude Code has ever run in shows
  up in the project list, including one-off experiments and folders that no longer exist
  as work. A project can now be hidden from the TUI project menu and the GUI sidebar —
  from `Hide / restore projects` in the main menu, or the `Hide` button on a project page.
  It is a view flag (`project_defaults[<enc>].hidden`), so nothing on disk moves: the
  sessions stay resumable and restoring costs one click. The TUI says how many rows are
  filtered; the GUI grows a `Show N hidden projects` button while any are.
- **The Skills section shows what Claude Code actually loads.** It listed claudectl's own
  private library (`~/.claude/claudectl-skills`) — a directory nothing reads — beside a
  "Project skills" card that could never fill, because opening a global page ends project
  context. Now it is the four scopes Claude Code really resolves (personal, project,
  plugin, bundled), each row carrying the command you type and its real usage count from
  Claude Code's own `skillUsage` counters, plus the starters to install from. What was in
  the private library is migrated into `<account>/skills` once, and `sync-accounts` levels
  skills across accounts like everything else.
- **Desktop notifications** when a background job that ran longer than 20s finishes, and
  when the detached memory worker is done — which had no interface of any kind before.
  One hook in the job runner, one in the worker; `Settings → Notifications` turns them off.
- **Loops, including ones that run with nothing open.** A `/loop` is session-scoped — it
  fires only while its session is open and idle — so claudectl offers both: start one *in a
  session* (and watch it through that session's transcript), or *in the background*, where
  claudectl registers an entry in Task Scheduler (cron elsewhere) that runs headless
  `claude -p` on the interval, under the account you pick, with claudectl closed. A
  background loop carries its guardrails in the runner rather than the UI: a permission mode
  you choose (`claude -p` starts in Manual and would otherwise do nothing), a 7-day expiry
  that the scheduled run enforces on itself, your per-call budget cap on every run, a
  notification when one fails, and a log of the last twenty runs with their cost. Each run
  is a fresh session that reads a rolling `CLAUDECTL:LOOP` record in the project's
  CLAUDE.md — rewritten every time, capped at five entries, so it cannot grow. `loop.md` is
  edited on the same page for either scope, with an AI draft behind the usual approval gate.
- **Agents that actually get delegated to.** Copying agent files into a project makes them
  available; Claude Code still picks a subagent by matching the task against its
  `description`, and nothing reads the body — measured here, `agentLastUsed` held one entry
  against ten installed agents. Four levers now: a `CLAUDECTL:AGENTS` delegation table in
  the project's CLAUDE.md (the one file read on every turn), **Sharpen descriptions** (one
  Claude call rewrites each installed agent's description into *Use PROACTIVELY when …*
  form, diff-approved, bodies untouched), an optional `suggest-subagent` prompt hook that
  names a keyword match with no model call and stays silent otherwise, and a last-used
  marker in the picker so the dead weight is visible.
- **Output styles explain themselves**, in scope order with the active one and the file
  that pins it named, and four claudectl starters (Terse, Reviewer, Pair, Ship) to copy.
- **Global CLAUDE.md has its own page.** It was the third card at the bottom of the MCP
  servers page.
- **Search boxes** on the agent library, the project agent picker and the skills inventory.
- **Skills usage that means something.** `skillUsage` counts one thing — the times you
  *typed* `/name` — so a plugin that works through a `SessionStart` hook reads as unused
  forever (caveman: "used twice, 56 days ago", while shaping every session). The page now
  shows that counter merged across **every** account beside a second, measured signal from
  your transcripts: *in 27 of your last 30 sessions*. Two flags cover the rest — **manual
  only** (`disable-model-invocation`) and **thin description**, the two reasons a skill
  silently never fires. Personal skills install into, and delete from, every account.

- **Auto-memory that actually runs, and converges.** Turned on per project (memory hub
  `o`, or the checkbox on the GUI memory tab — one flag now honoured by both interfaces
  *and* the detached worker), a project's memory no longer goes stale while it is on. Each
  cycle extracts what its budget allows and leaves the rest queued; the scheduler returns
  in seconds rather than after the full interval until the project has caught up. It
  bootstraps a project with no graph at all, so the first build no longer has to be
  manual, and it runs from the TUI as well as the GUI.
- **A stale-on-edit hook.** `memory-stale-on-change` records the files Claude edits, so
  auto-memory re-extracts exactly those instead of walking the project. The periodic scan
  remains the reconciler for edits made outside Claude Code.
- **Nothing shrinks without a way back.** Pin any entity or lesson and the importance cap
  can never evict it. Fence a section of CLAUDE.md between `CLAUDECTL:KEEP` markers and AI
  compression never even *sends* it to the model. Prune names the exact session entries it
  will drop and asks first — the GUI destroyed silently while the TUI confirmed. Twelve
  versions of CLAUDE.md and of the memory graph are kept, browsable with a diff and
  restorable from the context-audit page; restoring is itself snapshotted.
- **"What to work on" is worth reading.** Four new free signals (stale context with the
  key that clears it, `TODO`/`FIXME` markers, deferred `ponytail:` shortcuts, untested
  modules) plus an optional one-call *Find work* scan that adds bugs, vulnerabilities,
  slow paths and functions worth building. Findings persist, so the card stays instant.
- **Sharpen descriptions is machine-wide.** It lived on one project's tab and rewrote that
  project's agents only. It now covers every account's agents, every project's, and the
  library — grouped so the same agent in twelve projects is one question and twelve
  writes, behind one approval gate.
- **Controls for the memory settings that had none.** Seven of fourteen lived only in
  `claudectl.json`, including `memory_max_calls`, which the memory hub and the health
  check both told you to raise.
- **Per-cycle cost, and what a cycle actually did**, in the memory hub and the GUI. The
  real figure was captured from every headless call and read by nothing.

### Fixed

- **Auto-memory did nothing at all once more than six modules had changed** — the harder
  you worked, the less it updated — and it could never build a project that had no graph
  yet, so "keep this updated automatically" required a manual build first. The GUI
  checkbox wrote a flag only the GUI scheduler read, while the TUI and the worker gated on
  a setting with no control on either surface, so ticking the box did nothing outside a
  running GUI window.
- **A failed extraction wiped a module's memory and never retried it.** A Claude call that
  timed out returned an empty result indistinguishable from "this module has nothing", so
  every fact already known about it was marked superseded — and its file hashes were
  recorded as current, making the loss invisible to every later check. A capped run did
  the same to every module it skipped. Provenance now advances only for modules actually
  extracted, and `save_memory` failing is no longer ignored at five call sites.
- **The staleness check read every source file in the project**, in full, to hash it — on
  every scheduler tick and every project open. Files are compared by `(mtime, size)` first
  and re-hashed only when that moves; content stays the source of truth, so touching a
  file still costs no Claude call.
- **A memory refresh reported success after crashing.** The GUI badge read the scan lock
  disappearing as completion, which a failed cycle does exactly like a successful one; the
  detached worker announced "Memory updated" when nothing had run, and told nobody at all
  when it failed.
- **The query `the` returned 33 entities.** The recall IDF could never reach zero and a
  positive score was the only gate, so with the prompt hook on, memory was injected into
  prompts that asked for none. Ranking now fuses four signals by position (BM25, path,
  dependency rank, and a confidence-weighted lesson signal) instead of adding four
  quantities on four different scales, and a contentless prompt retrieves nothing.
- **Recall reinforcement had never worked.** The counter that decides what eviction keeps
  was folded in only by a refresh that had work to do — so on this repo 807 recorded hits
  had produced a maximum counter value of 1 — and it credited every entity that ranked
  rather than the ones that fit the budget and were actually injected.
- **One generated rule file was always loaded and another could never load.** The
  path-scoped glob was built with a *character* prefix, so `{tests/, tools/}` became
  `t/**` (matching nothing, ~375 tokens that never loaded) while a unit whose files
  diverged at the first segment became `**` — permanently in context, in the one feature
  whose whole purpose is laziness.
- **The `memory-stale-on-change` preset had never marked anything stale.** It was bound to
  an event whose matcher is a list of literal filenames, and it invoked a script that does
  not touch memory.
- **A workspace could not stop reporting itself stale.** Only two operations recorded a
  freshness baseline, so rebuilding memory — the very thing the screen tells you to do —
  could never clear it. Checks that contribute nothing to the score no longer show a
  warning dot, and every missing point now prints what recovers it.
- **claudectl could rename Claude Code's live `.claude.json` out from under it.** Reading
  a file another program is writing occasionally catches a half-written one, and the
  corrupt-file quarantine treated that as damage worth preserving.
- **Cross-project conventions was permanently empty** and described inputs it does not
  read: it scanned one account, one directory deep. It now reads every account, accepts
  `decision` lessons, and lists near-misses with a Pin button instead of a dead end.
- **A finished job no longer repaints the page you moved to.** `onDone` handlers called
  `drawMemory()` / `drawPage(…)` unconditionally, so building memory and walking away put
  you back on the memory tab minutes later, over whatever you had opened. The refresh is
  now a guarded `redraw` in the job runner, gated on the view the job started from.
- **The `view` button on a built-in output style showed "(empty)"** — those ship inside
  Claude Code and have no file, which read as a broken button.
- **Selecting an output style with no project open threw**, because every write assumed
  `CUR.path`. The same bug made the Skills and Output styles pages' project sections
  structurally unreachable: navigating to a global page clears the current project, so the
  "this project" card could never have shown anything. They use the last opened project and
  say which one it is.
- **A folded YAML description (`description: >-`) parsed as the literal `>-`**, so every
  plugin skill looked as if it had no description at all.
- `load_settings()` copied the defaults shallowly, so every load of a settings file that
  named no `project_defaults` handed back the same dict — and each writer of one mutates
  it in place. In a long-lived process (the GUI, a long TUI run) one project's pins leaked
  into the next load.

## [1.7.0] - 2026-08-28

### Added

- **What you provision now reaches every account, not just the default one.** Hooks,
  plugins, marketplaces, user agents and the global `CLAUDE.md` were all written into
  whichever config directory happened to be active when the module was imported — in
  practice the default account. Measured across five configured logins, the default had
  18 hooks, 3 marketplaces, 3 plugins and a global `CLAUDE.md`; the other four had none of
  it. The status line was the only feature that ever reached all of them, because it was
  the only one with a real fan-out; that fan-out is now the rule rather than the exception.
- `claudectl sync-accounts` levels every account up to the union of them all, on the
  command line, on the GUI's Accounts page and in the TUI accounts menu. It shows the
  per-account diff **before** writing anything, only ever adds (an account keeps whatever
  the others lack, because nothing can tell a deliberate choice from a gap), routes every
  plugin install through the same review gate a single-account install uses, and reports
  nothing to do when re-run.
- The Plugins page says which accounts have each plugin and marketplace, and offers to
  install one into the accounts that do not. Adding a marketplace registers it everywhere;
  removing acts only on the account on screen.
- **Nineteen functions that existed only in the terminal UI now have a surface in the
  GUI**: the editor / `claude.exe` / config-dir paths and the headless budget cap; disabled
  hooks, an enable/disable control and an *Edit settings.json* button; the per-prompt
  recall hook, path-scoped rules and the recall budget, which the GUI had been printing
  read-only; the global `CLAUDE.md` viewer and editor; `claude mcp get` detail and MCP tool
  docs, which the analyze job used to write into a file the GUI could not open; env vars
  and headers when adding an MCP server; the one-key project setup the terminal offers as
  `!`; the architecture stats card, which revives an endpoint that had no consumer at all;
  tools and model when creating an agent; copying a skill into your library; and a Help
  page generated from the navigation and the terminal's own key table instead of retyped.
- Cross-project conventions, `loop.md` at both project and account scope, Claude Code's own
  per-project record and output-style previews — four endpoints that had no control in
  either surface — are reachable.

### Fixed

- `claude plugin …` and `claude mcp …` ran with no environment, so they acted on whatever
  account the process inherited while every reader resolved the configured one: with
  claudectl switched to another account, the Plugins page listed that account's plugins and
  *Install* wrote into the default. Both now name an account explicitly.
- `mcp.update_global_claude_md_mcp` and the conventions sync wrote a file Claude Code reads
  every session with a plain `open(..., 'w')`. A write that dies partway broke the whole
  session, not just claudectl. Both are atomic now.
- A hook disabled in the terminal vanished from the GUI, which then offered its template as
  uninstalled and created an enabled duplicate beside the disabled one.
- The job list on the dashboard raised as soon as any job had said anything: every producer
  appends a `{ok, text}` record and the reader indexed it as a string.
- The per-project recall toggle promised a hook that only one account had installed.

### Changed

- The GUI's accepted-settings list is derived from the settings registry minus an explicit
  internal set, rather than being a sixth hand-maintained copy that had already fallen
  behind it; the terminal's settings rows are named after the settings they write, so the
  two screens can be compared by a test instead of by hand.
- New parity gates, each watched failing under mutation: every route the SPA never calls
  must carry a written reason, a POST route must be reached with `post(`, a value rendered
  as on/off must be one the page can send back, the main menu's rows must name a GUI
  counterpart, and the account fan-out helper must have callers. The browser smoke tool
  walks the page and tab lists derived from the app instead of a hardcoded subset, and its
  floor rose from 40 checks to 85.

### Fixed

- The launch picker's effort slider pointed at the wrong label. `EFFORTS` had grown a
  seventh entry (`ultracode`) but the tick row was six labels typed into the markup, so
  the thumb at *xhigh* — 4/6 of the track — sat under HIGH, and `ultracode` was a stop
  with no label at all. The row is generated from the same list that sizes the slider,
  and each label is placed on its own stop rather than spread with `space-between`, which
  aligns label boxes and drifts every centre off its tick. `tools/shot_gui.py` now
  measures thumb-against-label for every stop.

### Added

- The sessions screen's 29 keys are described in one place instead of three. The action
  table, the `/` palette and the help screen had drifted apart — the palette was missing
  code review (`R`) and the help screen was missing `R`, plan→execute (`X`) and context
  injection (`K`), so code review had no discoverable entry point anywhere in the product.
  Both discovery surfaces are now generated from the table, and a test walks the key
  handlers in the source and fails when one of them has no row.
- The launch picker reads the model list from Anthropic instead of a hand-edited table, so
  a model released this week is selectable without a claudectl release. It uses the login
  Claude Code already holds — no API key — and refreshes once a day on a background thread.
  Facts (context window, supported efforts, release date) come from the API; cost rank,
  capability rank and the "best for" prose stay curated and are inherited by family, so a
  new generation appears with sensible columns rather than blanks. Every failure — no
  network, logged out, `auto_update: off` — falls back to the list shipped with this
  version, never to an empty picker, and a model you have pinned that Anthropic has retired
  keeps its place in the picker with a warning instead of being silently reset to *default*.
- claudectl reports its own version against PyPI and can update itself. A banner says when a
  newer release exists, the Updates screen and the Plugins page offer the upgrade, and
  `Settings → Updates` chooses between being told, installing on quit, and never checking.
  The upgrade runs in its own window after claudectl exits, because pip cannot rewrite the
  console script of the process running from it — and a git checkout is told to `git pull`
  rather than having a release installed over the top of it.
- Claude Code plugin distribution — `claudectl` installs as a plugin, with its bundled
  skills generated from the packaged templates and validated in CI.
- Multi-repo worktrees board and repo discovery, including submodule and linked-worktree
  classification read from the `gitdir:` pointer rather than a subprocess.
- Model failover proxy with TUI and GUI settings, so a dead model is retried instead of
  hanging the session.
- Desktop GUI overhaul: dashboard home, always-on account usage, a single 3D background
  stage, chassis skins and worlds, and an instrument dashboard.
- OmniRoute-aware dashboard and usage views, including free-tier session tagging.
- Resumable saved plans in Plan → Execute.
- Status line, installable per account.
- macOS and Linux support alongside Windows.

### Changed

- TUI/GUI parity is enforced as a CI gate rather than reviewed by hand; the API reference
  in `docs/api.md` is generated from the live route tables and checked for staleness.
- Per-turn work moved off the hot path — the status line dispatches before the TUI is
  imported, and hooks no longer re-stream whole transcripts on every turn.
- Duplicated primitives consolidated into single implementations for transcript reading,
  process control, JSON storage and session paths.
- Default models updated to Opus 5.
- README rewritten around real screenshots.

### Fixed

- Status line resolves the active account and project rather than the checkout it was
  installed from, and reports why an installed status line is not showing.
- Account-scoped settings writes no longer bind the config directory at import time, so
  every account is written correctly.
- Both local HTTP servers authenticate requests, with a host allowlist, browser
  fetch-metadata rejection and a per-run secret.
- State writes are atomic, and a file that exists but will not parse is quarantined
  instead of silently erased.
- Malformed requests return 400 instead of 500, and `cfgdir` is validated against known
  accounts.
- Session paths are resolved from the transcript's recorded `cwd` rather than decoded from
  the encoded folder name, which had dropped UNC-path projects entirely.
- Packaging: the skills templates glob now matches, so the wheel ships them; a CI job
  installs the built wheel and reads the data files back out of site-packages.
