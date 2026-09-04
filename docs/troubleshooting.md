---
description: >-
  Common claudectl problems and their fixes — missing claude.exe, editors that don't open,
  crashes, missing projects, wrong account, stale usage stats.
---

# Troubleshooting

| Symptom | Fix |
|---------|-----|
| "claude.exe not found" screen on startup | Install [Claude Code](https://docs.anthropic.com/claude-code), or set the path in **⚙ Settings** |
| Generated files don't open in an editor | Set your editor path in **⚙ Settings** (auto-detects Notepad++, VS Code, falls back to Notepad) |
| Window closes instantly with an error | Check `%TEMP%\claudectl_crash.log` — the crash handler writes the traceback there |
| Projects missing from the list | The project folder was moved/deleted, or the path can't be decoded — see [Session encoding](reference.md#session-encoding) |
| Wrong account / want a second account | Set **Config dir** in **⚙ Settings** to that account's `CLAUDE_CONFIG_DIR` (e.g. `~/.claude-work`). Drives both session browsing and the env handed to `claude` at launch. Blank = default `~/.claude`. Restart claudectl to apply. See [Multiple accounts](accounts.md) for running two at once. |
| Settings location | `~/.claude/claudectl.json` — safe to edit by hand or delete to reset (always read from `~/.claude`, independent of Config dir) |
| Usage stats look stale | Delete `~/.claude/claudectl-stats-cache.json` — it rebuilds on the next scan |
| GUI window tears or flickers | Set `stage: lite` in Settings first; if it persists, `stage: off`, or launch with `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu-compositing` |
| GUI flickers **while it is in the background** | Fixed — see [Flicker while the window is unfocused](#flicker-while-the-window-is-unfocused) below. If it survives an upgrade, the same `stage: lite` → `stage: off` → `--disable-gpu-compositing` ladder applies |
| OmniRoute free execution not working | See the [Plan → Execute troubleshooting](plan-execute.md#troubleshooting) section |
| Something failed and said nothing | Open **⚙ Logs** (TUI) or the **Logs** page (GUI). Background jobs, the auto-memory scheduler and claudectl's own Claude calls all record their failures there |
| "No output from Claude" when generating an agent / skill / CLAUDE.md | Usually the account's limit. claudectl now says so by name and offers another account — see [Rate limits and a second account](tui.md#rate-limits-and-a-second-account) |
| An AI feature refuses to run | The active account's session or weekly window is full. Pick another account when asked, set `headless_quota` to `auto`, or to `off` to launch regardless |

The rows below have their own sections, because their symptom is an exact string you would
paste into a search box.

## "claude.exe not found"

claudectl looks for the Claude Code CLI on your `PATH` and at `~/.local/bin/`. This screen
means neither had it.

- Install [Claude Code](https://docs.anthropic.com/claude-code) if you have not, then
  reopen claudectl — the check runs at startup, not once.
- If it *is* installed, the shell that launched claudectl has a different `PATH` than the
  one you installed it from. Run `where claude` (Windows) or `which claude` and put the
  full path into **⚙ Settings → Claude path**.
- On Windows the executable is often `claude.cmd` or a `claude.exe` shim in
  `%APPDATA%\npm`. Point at whichever `where claude` prints; claudectl runs it as-is.

claudectl never installs or updates Claude Code, and never writes your credentials.

## "No output from Claude"

An AI feature — generating an agent, a skill, a hook, or a CLAUDE.md block — says this
when the headless `claude` call returned nothing at all.

Almost always the account's limit. claudectl names the account and offers you another one.
If you have only one account configured, wait for the window to reset, or set
`headless_quota` to `off` in Settings to launch regardless and see the real error.

If it happens with quota to spare, the `claude` invocation is failing before it writes
anything. **⚙ Logs** records what `claude` printed on stderr for every failed call, which
is the line that tells you why.

## The GUI window tears, flickers or shows white bands

Only ever the Qt desktop shell, never the browser GUI. QtWebEngine composites through a
GPU surface on Windows and some drivers tear on the swap.

Try in this order, stopping at the first that works:

1. **⚙ Settings → `stage: lite`.** Drops the post-processing chain and its two render
   targets. This fixes it on most hardware and keeps the background.
2. **`stage: off`.** Falls back to a static gradient; nothing animates behind the app.
3. **`motion: off`.** Stops every animation, including the ones inside the interface.
4. Launch with `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu-compositing`. This routes the
   whole page through the CPU compositor — correct everywhere, and noticeably slower.
   claudectl does not override the variable if you have already set it.

If the *browser* GUI flickers too, it is not this — please
[open an issue](https://github.com/babarmuhammad/claudectl/issues) with your GPU and
browser, because that is a different bug.

## Flicker while the window is unfocused

A different problem with the same word, and it affected every shell — Qt, Edge and the
browser — because the cause was the page, not the compositor. If claudectl flickered while
you were working in *another* app, this was it.

While the window is not focused, claudectl now:

- **stops every looping animation** (spinners, progress beams, activity pips, the CRT
  caret, a world's scanline overlay) and resumes them where they left off on return;
- **removes a world's full-viewport overlay** entirely — it was a fixed layer on a 1.1s
  loop that nothing but `motion: off` used to take down;
- **keeps painting the background colour** during the swap that hides the WebGL canvas.

That last one was the visible flash. The canvas is taken out of the composite on blur (a
visible-but-unrendered GL surface is what tears under Qt), the two static washes fade back
in over half a second, and for that half second there was nothing painting the ground.

Chromium only throttles CSS animations for a *hidden* page. A window you have alt-tabbed
away from is not hidden — least of all under Qt, which reports even a minimised one as
visible — so none of this stopped on its own.

Nothing to configure. `motion: off` and `stage: off` still do what they always did.

## A project is missing from the list

Usually the folder moved or was deleted. claudectl lists the projects Claude Code has
recorded and does not invent them, so launch a session in the new location once and it
comes back.

The other cause is a path claudectl could not resolve. It reads the real `cwd` out of each
transcript rather than decoding the folder name, so a project folder with transcripts in
it always resolves; one with none has nothing to read. See
[Session encoding](reference.md#session-encoding).

## Usage or repository numbers look stale

For usage stats, delete `~/.claude/claudectl-stats-cache.json` and it rebuilds on the next
scan.

A repository's dirty flag is different. The status line serves git state from a disk cache
and never spawns git on a turn, because that would cost you the round trip on every
message. Branch, ahead and behind stay current, since all three invalidate off files under
`.git`. Only *dirty* can lag: editing a tracked file touches nothing git watches. Open the
Repos board to refresh it.

## Where the logs are

| File | What it holds |
|------|---------------|
| `~/.claude/claudectl-events.jsonl` | **Start here.** claudectl's own events — failed Claude calls with what `claude` actually printed, job crashes, scheduler passes, quarantined state files. Capped at 256 KB. Shown by the Logs screen in both interfaces |
| `%TEMP%\claudectl_crash.log` | The traceback when the window closes instantly |
| `%TEMP%\claudectl.log` | Verbose DEBUG tracing — **only** written when `CLAUDECTL_DEBUG=1` is set. Unbounded; turn it on for one run |
| `~/.claude/failover.log` | The local failover proxy's requests, when it is running |

Still stuck? [Open an issue](https://github.com/babarmuhammad/claudectl/issues) — and check
the [FAQ](https://claudectl.space/faq/) first.
