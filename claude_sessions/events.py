"""claudectl's own event log — what it did, and what failed.

Until this existed, a failure inside claudectl was structurally invisible: the
`claudectl` logger carried a NullHandler unless `CLAUDECTL_DEBUG` was set, so
every background job crash, every faulted API handler, the bg-scan worker and
the failover proxy all wrote to nothing. The motivating case is the one the
comment in `gui_api._run_cancellable` already describes — a rate-limited
account producing six silent failures an hour, reported as "queued".

One append-only JSONL under `~/.claude/` (account-independent, like
`claudectl.json` itself and like `failover.log`), because the interesting
failures are cross-process and usually belong to no project at all: the
scheduler, the proxy, the detached scan worker.

Two rules keep it cheap enough to leave on:

* **Nothing on a per-turn path writes here.** Every writer is a claudectl-owned
  process (TUI, GUI server, scheduler, bg-scan, proxy). No hook records an
  event — the recall hook already taught this project what a per-prompt write
  costs.
* **`record()` never raises and never logs.** It sits on the error path; a
  writer that can fail is a second bug on top of the one being reported, and a
  writer that logs would recurse through the handler that called it.

Reading is `transcripts.iter_json` — the one streaming reader for this format,
exactly as `automode.denials` already does for `denied.jsonl`.
"""

import json
import os
import re
import time

from . import config as _c

__all__ = ['path', 'record', 'read', 'MAX_BYTES']

#: bounded in BYTES for the same reason automode's denial log is: an event has
#: no characteristic length, and a stack trace is not the same size as "claude
#: exited 1". 256 KB rather than that log's 64 KB because this one is the whole
#: application rather than one project's denials.
MAX_BYTES = 256 * 1024
_KEEP_BYTES = MAX_BYTES // 2

LEVELS = ('error', 'warn', 'info')

#: identical (src, msg) inside this window is dropped. failover warns once per
#: failed upstream request, so a 429 outage would otherwise churn the entire cap
#: in minutes and push every other event out of the file.
# ponytail: in-process dedupe loses the repeat count; carry an `n` if it matters
_DEDUPE_SEC = 60
_recent = {}

#: DECIMALS only. Collapsing integers too was over-broad: `failover` logs
#: 'HTTP %s' so a 429 and a 500 became one event, `gui_api` logs '... failed
#: for %s' so two project paths differing by a digit collapsed and the
#: second project's failure was silently dropped, and `diskgc` logs the
#: rejected path. The measured defect was 'slow: %.2fs' — a decimal.
_NUMS = re.compile(r'[0-9]+\.[0-9]+')


def _dedupe_shape(msg):
    """The message with every number collapsed, for the dedupe key only.

    A key that contains a measurement is not a key. `gui.py` formats the elapsed
    time into its slow-handler warning, so `slow: 1.55s` and `slow: 1.56s` were
    different messages and the window above never once fired: 609 of the 674
    events in this machine's log were slow-warnings differing only in the
    decimal, and they had pushed every real failure past MAX_BYTES. Collapsing
    digits fixes that for every numeric-varying message, not just this one —
    elapsed times, byte counts, PIDs, session ids.

    Only DECIMALS collapse. An integer usually IS the identity of the event —
    an HTTP status, a PID, a port, a digit inside a path — and folding those
    turned two different failures into one dropped one of them.

    The WRITTEN msg keeps its numbers; only the memo key is shaped.
    """
    return _NUMS.sub('#', msg)


def path():
    """`~/.claude/claudectl-events.jsonl` — beside claudectl.json, and
    account-independent for the same reason it is: an event from the scheduler
    or the proxy belongs to the machine, not to whichever account happened to
    be active."""
    return os.path.join(os.path.dirname(_c.settings_file),
                        'claudectl-events.jsonl')


def record(src, msg, *, level='error', detail='', proj=''):
    """Append one event. Returns True when it was written.

    Truncation happens HERE rather than at the call sites: a caller reporting a
    failure should not also have to remember the budget.
    """
    try:
        msg = str(msg or '').strip()
        if not msg:
            return False
        level = level if level in LEVELS else 'error'
        key = (str(src), _dedupe_shape(msg))
        now = time.time()
        last = _recent.get(key)
        if last is not None and now - last < _DEDUPE_SEC:
            return False
        _recent[key] = now
        if len(_recent) > 500:                     # bounded memo, oldest out
            for k, _ in sorted(_recent.items(), key=lambda kv: kv[1])[:250]:
                _recent.pop(k, None)
        line = json.dumps({'ts': round(now), 'lvl': level,
                           'src': str(src or '')[:40],
                           'msg': msg[:200],
                           'detail': str(detail or '')[:1000],
                           'proj': str(proj or '')[:80]}, ensure_ascii=False)
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        _rotate(p)
        return True
    except Exception:
        return False


def read(limit=200):
    """Most recent first. Each: {ts, lvl, src, msg, detail, proj}."""
    p = path()
    if not os.path.isfile(p):
        return []
    out = []
    try:
        from . import transcripts
        for obj in transcripts.iter_json(p):
            out.append(obj)
    except Exception:
        return []
    return list(reversed(out))[:limit]


def _rotate(p):
    """Drop the oldest half once the log passes MAX_BYTES. Size-gated, so the
    common append costs one getsize and no rewrite."""
    try:
        if os.path.getsize(p) <= MAX_BYTES:
            return
        with open(p, 'rb') as f:
            f.seek(-_KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        tail = tail.split(b'\n', 1)[1] if b'\n' in tail else tail
        with open(p, 'wb') as f:
            f.write(tail)
    except Exception:
        pass


# ── TUI screen ───────────────────────────────────────────────

def _fmt_time(ts):
    try:
        return time.strftime('%d %b %H:%M', time.localtime(float(ts)))
    except (TypeError, ValueError):
        return '?'


def _lines(evs, width):
    """Render events as pager lines. Colours are read off the config module at
    call time, never bound at import — apply_theme rebinds them."""
    from . import render
    lv_col = {'error': _c.C_ERR, 'warn': _c.C_WARN, 'info': _c.C_OK}
    out = []
    for e in evs:
        lvl = str(e.get('lvl') or 'error')
        col = lv_col.get(lvl, _c.C_ERR)
        head = (f"  {_c.C_DIM}{_fmt_time(e.get('ts'))}{_c.C_RESET}  "
                f"{col}{lvl.upper():<5}{_c.C_RESET} "
                f"{_c.C_TITLE}{str(e.get('src') or '?')[:18]:<18}{_c.C_RESET} ")
        out.append(head + render.trunc(str(e.get('msg') or ''), max(20, width - 42)))
        if e.get('proj'):
            out.append(f"      {_c.C_DIM}{render.trunc(str(e['proj']), width - 10)}{_c.C_RESET}")
        for d in str(e.get('detail') or '').splitlines():
            if d.strip():
                out.append(f"      {_c.C_DIM}{render.trunc(d.strip(), width - 10)}{_c.C_RESET}")
        out.append('')
    return out


def logs_screen():
    """What claudectl did and what failed, newest first.

    A pager rather than a hand-drawn list: the stream is flat and nothing in it
    is actionable, and the pager's in-content search already IS the filter.
    """
    from .ui import pager, flash
    from . import render
    while True:
        evs = read()
        width = render.content_width()
        if evs:
            lines = _lines(evs, width)
            counts = {}
            for e in evs:
                counts[e.get('lvl')] = counts.get(e.get('lvl'), 0) + 1
            summary = '  '.join(f"{k}: {v}" for k, v in counts.items() if k)
            header = [f"  {_c.C_DIM}{len(evs)} events   {summary}{_c.C_RESET}",
                      f"  {_c.C_DIM}{render.trunc(path(), width - 4)}{_c.C_RESET}", '']
        else:
            lines = ['', '  Nothing recorded yet.', '',
                     f"  {_c.C_DIM}claudectl writes here when one of its own Claude calls,",
                     f"  background jobs or scheduled passes fails.{_c.C_RESET}", '']
            header = []
        key = pager(('CLAUDECTL', 'LOGS'), lines,
                    hint='e  open the raw file   /  search',
                    header_lines=header, extra_keys=('e',))
        if key != 'e':
            return
        if os.path.isfile(path()):
            _c.open_in_editor(path())
        else:
            flash('Nothing recorded yet', ok=False, secs=1.2)
