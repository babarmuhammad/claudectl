"""GUI API layer — jobs + endpoint handlers for full TUI parity.

Two ideas make this thin:

1. Every handler calls the SAME pure functions the TUI screens call
   (scan/load/save helpers) — no logic is duplicated here.

2. Long-running AI features (memory build, AI CLAUDE.md, MCP analysis, …)
   run the UNCHANGED TUI functions on a background job thread with a
   thread-local **UI bridge**: `flash` becomes a job message, progress runs
   headless (`memory._tls.silent`), and the `diffview.confirm` /
   `_pager_confirm` approval gates park the job in 'awaiting' status until
   the GUI posts an approve/reject decision. Exact behavioral parity —
   including the write-after-approve semantics — with zero forked code.
   Outside a job thread every bridge falls through to the original TUI
   implementation, so the TUI is untouched.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid

from . import config as _c
from . import proc as _proc     # `proc` is a local name for a Popen throughout
from . import store as _store


# ── job model ────────────────────────────────────────────────

_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBCTX = threading.local()          # .job set on job threads


class JobCancelled(Exception):
    """Raised inside a job thread when the user cancels."""


def _claude_failure_reason(stdout):
    """The sentence a human needs, out of what `claude -p` printed before it
    exited non-zero.

    `--output-format json` (which `memory._claude_json` asks for) puts the
    refusal in `result`, behind ~200 characters of `duration_api_ms`,
    `stop_reason`, `session_id`, `total_cost_usd` and `usage`. Everything that
    reports a failure truncates, so what actually reached the user — the job
    banner, the Logs page, the event log — was a clipped JSON blob with the
    reason cut off. Twenty of those are sitting in this machine's event log, and
    every one of them means "You've hit your session limit · resets 2:30am".

    Falls through to the raw text unchanged when stdout is not that envelope
    (`--print`, a crash, a stack trace), so nothing is hidden.
    """
    raw = (stdout or '').strip()
    if not raw.startswith('{'):
        return raw
    try:
        env = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(env, dict):
        return raw
    for key in ('result', 'error', 'message'):
        v = env.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):                     # {"error": {"message": ...}}
            m = v.get('message')
            if isinstance(m, str) and m.strip():
                return m.strip()
    return raw


def _run_cancellable(cmd, input_text=None, capture_output=True, text=True,
                     encoding='utf-8', errors='ignore', cwd=None, env=None,
                     timeout=600):
    """subprocess.run replacement that honours the current job's cancel_event.

    Returns stdout string (or '' on cancel/failure). Raises JobCancelled if
    the user cancels while the subprocess is running."""
    job = getattr(_JOBCTX, 'job', None)
    if job and job.get('cancel_event', threading.Event()).is_set():
        raise JobCancelled
    # Don't spend an account that has nothing left. A no-op for anything that
    # isn't `claude … -p`, and a no-op when the usage cache says nothing.
    from . import quota
    env, _blocked = quota.preflight(cmd, env)
    if _blocked:
        return ''
    try:
        # CREATE_NO_WINDOW: a captured child shows nothing in its console,
        # so the window is pure flicker. See proc.no_window_flags.
        from .proc import no_window_flags
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE if capture_output else None,
                                stderr=subprocess.STDOUT if capture_output else None,
                                text=text, encoding=encoding, errors=errors,
                                cwd=cwd, env=env, creationflags=no_window_flags)
    except Exception:
        return ''
    if job is not None:
        job.setdefault('procs', []).append(proc)
    killed = threading.Event()

    def _watch():
        if job is None:
            return
        while not killed.is_set():
            if job['cancel_event'].wait(timeout=1.0):
                break
        if not killed.is_set() and proc.poll() is None:
            _proc.kill_tree(proc)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    try:
        stdout, _ = proc.communicate(input=input_text, timeout=timeout)
        stdout = (stdout or '').strip() if capture_output else ''
        # stderr is merged into stdout above, so a failed CLI run looks exactly
        # like a successful one to every caller unless the exit code is checked.
        if proc.returncode:
            reason = _claude_failure_reason(stdout)
            if job is not None:
                job['last_subprocess_error'] = {'code': proc.returncode, 'output': reason}
            # The scheduler and the detached memory worker have NO job context,
            # so `if job is not None` dropped the reason on the floor for
            # precisely the two callers that run unattended — a rate-limited
            # account produced six silent failures an hour, reported as
            # "queued". Record it where any caller can read it.
            from . import memory as _mem
            _mem.last_call_error = 'claude exited %s: %s' % (
                proc.returncode, (reason or '(no output)')[:300])
            from . import events, quota
            # the ENVELOPE, not the extracted sentence: a marker could live in a
            # field the sentence does not carry, and this test is a cheap
            # substring scan over text we already have in memory
            quota.note_failure(env, stdout)
            events.record('subprocess', _mem.last_call_error,
                          detail=' '.join(str(c) for c in cmd[:2]))
            return ''
        return stdout
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        msg = ('timed out after %ss — upstream may be an unresponsive '
               'OmniRoute/failover endpoint' % timeout)
        if job is not None:
            job.setdefault('messages', []).append({'ok': False, 'text': msg})
        # a job's message list is not a record, and the two unattended callers
        # have no job at all — so the timeout was reaching nothing
        from . import events
        events.record('subprocess', msg,
                      detail=' '.join(str(c) for c in cmd[:2]))
        return ''
    except Exception:
        try: proc.kill()
        except Exception: pass
        return ''
    finally:
        killed.set()
        t.join(timeout=2)
        if job is not None and proc in job.get('procs', []):
            job['procs'].remove(proc)
        if job is not None and job['cancel_event'].is_set():
            raise JobCancelled


def _subprocess_error_detail():
    """What the last failed subprocess on this job thread actually said, so a
    caller can replace a generic 'no output' message with the real reason."""
    job = getattr(_JOBCTX, 'job', None)
    err = job and job.get('last_subprocess_error')
    if not err:
        return ''
    return 'claude exited %s: %s' % (err['code'], (err['output'] or '(no output)')[:400])


def _job(jid):
    with _JOBS_LOCK:
        return _JOBS.get(jid)


#: terminal jobs kept for the UI to read a result off; and the age past which a
#: job still claiming to run is a leak, not work in progress
_KEEP_TERMINAL = 50
_STUCK_AFTER = 6 * 3600


def _reap_locked():
    """Trim the registry. Call with _JOBS_LOCK held.

    Only terminal jobs used to be trimmed, so a 'running' or 'awaiting' job
    whose thread died in a way that left no terminal status stayed in the
    registry forever — and reaping ran only when a NEW job started, so an idle
    app never cleaned up at all.
    """
    now = time.time()
    for j in list(_JOBS.values()):
        if j['status'] in ('running', 'awaiting') and now - j['started'] > _STUCK_AFTER:
            j['status'] = 'error'
            j['error'] = j.get('error') or 'abandoned after %dh' % (_STUCK_AFTER // 3600)
    terminal = [j for j in _JOBS.values() if j['status'] not in ('running', 'awaiting')]
    if len(terminal) > _KEEP_TERMINAL:
        terminal.sort(key=lambda j: j['started'], reverse=True)
        for j in terminal[_KEEP_TERMINAL:]:
            _JOBS.pop(j['id'], None)


def new_job(label, jid=None, inputs=None, **over):
    """A job registry entry, with every field job_status expects.

    A factory rather than a literal because tests hand-built this dict and it
    went stale twice — once on `ended`, once on `result` — each time surfacing
    as a 500 from /api/dashboard rather than as an obviously wrong fixture.
    One definition of the shape, used by start_job and by anything else that
    needs one.
    """
    job = {'id': jid or uuid.uuid4().hex[:12], 'status': 'running',
           'label': label, 'messages': [],
           'result': None, 'error': '', 'gate': None,
           'decision': None, 'decision_evt': threading.Event(),
           'inputs': list(inputs or []), 'started': time.time(),
           # set once, when the job reaches a terminal status. Without it
           # `elapsed` for a finished job kept counting against the wall clock,
           # so a job that ran for 12 seconds an hour ago reported "3600s".
           'ended': 0.0,
           'cancelled': False,
           # its own lock: _JOBS_LOCK covers the REGISTRY, and four threads
           # (the job body, the poll loop, job_decide and job_cancel) mutate
           # the dict's contents — status, gate, decision, procs
           'lock': threading.RLock(),
           'cancel_event': threading.Event(), 'procs': []}
    job.update(over)
    return job


def start_job(label, fn, inputs=None):
    """Run fn() on a daemon thread under the UI bridge. Returns job id.
    inputs: queued answers for any text_input the flow asks for."""
    job = new_job(label, inputs=inputs)
    jid = job['id']
    with _JOBS_LOCK:
        _JOBS[jid] = job
        _reap_locked()

    def _run():
        from . import memory
        _JOBCTX.job = job
        memory._tls.silent = True
        try:
            job['result'] = fn()
            if job['status'] == 'running':
                job['status'] = 'done'
        except JobCancelled:
            if job['status'] != 'cancelled':
                job['status'] = 'cancelled'
        except Exception as e:
            _c.log.exception('gui job failed: %s', label)
            if job['status'] != 'cancelled':   # a user cancel already won — keep it
                job['error'] = str(e)
                job['status'] = 'error'
        finally:
            # A job thread must ALWAYS leave a terminal status. If the body died
            # some other way (BaseException such as a thread kill, or a failure
            # swallowed by an inner helper) with status still 'running', force
            # 'error' so the UI banner can never spin forever.
            if job['status'] == 'running':
                job['status'] = 'error'
                job['error'] = job.get('error') or \
                    'job ended without setting a terminal status'
            job['ended'] = time.time()
            _JOBCTX.job = None
            # THE place a background job ends, which is why the desktop
            # notification hangs here rather than on each of the thirty
            # start_job call sites. It notifies only for work that ran long
            # enough that the user has probably left the window.
            try:
                from . import notify
                notify.job_finished(job['label'], job['status'],
                                    job['ended'] - job['started'], job['error'])
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return jid


def job_status(jid):
    job = _job(jid)
    if not job:
        return None
    with job['lock']:
        out = {k: job[k] for k in ('id', 'status', 'label', 'messages', 'error')}
        out['messages'] = list(out['messages'])
        # how long it RAN, not how long ago it started. Whole seconds, because
        # the poll loop compares this string 10x/sec and a fractional value
        # churns the DOM (see the poll-flicker note in CLAUDE.md).
        # .get: this dict is built in one place but read by four threads, and a
        # job that has not finished simply has no end time yet
        ended = job.get('ended') or 0
        out['elapsed'] = int((ended or time.time()) - job['started'])
        out['started'] = int(job['started'])
        out['ended'] = int(ended)
        if job['status'] == 'awaiting' and job['gate']:
            g = job['gate']
            out['gate'] = {'title': g['title'], 'diff': g['diff'],
                           'old_len': len(g.get('old', '')),
                           'new_len': len(g.get('new', ''))}
            left = job.get('gate_deadline', 0) - time.time()
            out['gate_seconds_left'] = max(0, int(left))
        if job['status'] == 'done':
            out['result'] = _jsonable(job['result'])
    return out


def job_decide(jid, apply):
    """Answer a pending confirm gate."""
    job = _job(jid)
    if not job:
        return False
    with job['lock']:
        if job['status'] != 'awaiting':
            return False
        job['decision'] = bool(apply)
        job['status'] = 'running'
        job['decision_evt'].set()
    return True


def job_cancel(jid):
    job = _job(jid)
    if not job:
        return False
    with job['lock']:
        if job['status'] == 'awaiting':      # a cancel at the gate = reject
            job['decision'] = False
            job['decision_evt'].set()
        job['cancelled'] = True
        job['status'] = 'cancelled'
        job['cancel_event'].set()
        # snapshot under the lock: _run_cancellable's finally removes from this
        # list, so iterating it unlocked raced with its own cleanup
        procs = list(job.get('procs', []))
    for p in procs:
        _proc.kill_tree(p)
    return True


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


# ── UI bridge (installed once; no-op outside job threads) ────

def _install_bridge():
    # hooks/claude_md are imported here so they are certain to be in sys.modules
    # before the by-value sweep at the bottom runs — they are the two that were
    # actually caught holding their own copies.
    from . import ui, diffview, claude_md, hooks    # noqa: F401 (see the sweep)

    _orig_flash = ui.flash
    def flash(msg, ok=True, secs=1.0):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_flash(msg, ok=ok, secs=secs)
        job['messages'].append({'ok': bool(ok), 'text': str(msg)})
    ui.flash = flash

    _orig_confirm_dv = diffview.confirm
    def dv_confirm(old, new, title):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_confirm_dv(old, new, title)
        return _gate(job, title, old, new, diffview.unified(old, new))
    diffview.confirm = dv_confirm

    _orig_pager_confirm = claude_md._pager_confirm
    def pager_confirm(title, content):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_pager_confirm(title, content)
        # splitlines, not the raw string: gate['diff'] is rendered with .map() in
        # the browser, and a string is truthy so the `||[]` fallback never fires
        # — it threw before the Approve/Reject handlers were wired, leaving the
        # job parked with no way to resolve it.
        return _gate(job, title, '', content, content.splitlines())
    claude_md._pager_confirm = pager_confirm

    _orig_text_input = ui.text_input
    def text_input(prompt, default=''):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_text_input(prompt, default=default)
        return job['inputs'].pop(0) if job['inputs'] else default
    ui.text_input = text_input

    _orig_ui_confirm = ui.confirm
    def ui_confirm(prompt, danger=False):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_ui_confirm(prompt, danger=danger)
        return True     # GUI flows pre-confirm destructive actions client-side
    ui.confirm = ui_confirm

    # ── the by-value copies ──────────────────────────────────────────────
    # `from .ui import flash, text_input, confirm` binds a SECOND name, and
    # patching `ui` does not move it: `hooks._ai_hook` hung on the real
    # `confirm`, and `claude_md`'s `_on_job_thread()` branch hung on the real
    # `text_input` — the exact job the split was written to un-hang.
    #
    # Naming the modules is what has failed every previous time this bug
    # appeared (`config._spawn_editor`, `hooks.settings_path`), so this does not
    # name them: it sweeps for the ORIGINAL function object, which is exact —
    # no list to fall behind, and no false positive. Modules imported after this
    # point need nothing, because by then `ui.flash` already IS the bridge.
    swap = {id(_orig_flash): flash, id(_orig_text_input): text_input,
            id(_orig_ui_confirm): ui_confirm}
    for m in list(sys.modules.values()):
        if not getattr(m, '__name__', '').startswith(__package__ + '.'):
            continue
        for attr, val in list(vars(m).items()):
            if id(val) in swap and m is not ui:
                setattr(m, attr, swap[id(val)])


#: how long an approval gate waits before rejecting itself. It was an hour, on
#: the theory that a user might wander off — but a job parked for an hour is
#: holding a worker thread and, usually, a claude subprocess. The countdown is
#: reported in job_status so the wait is visible rather than mysterious.
GATE_TIMEOUT = 300


def _gate(job, title, old, new, diff):
    """Park the job until the GUI approves/rejects the proposed content."""
    with job['lock']:
        job['gate'] = {'title': title, 'old': old, 'new': new, 'diff': diff}
        job['decision'] = None
        job['decision_evt'].clear()
        job['gate_deadline'] = time.time() + GATE_TIMEOUT
        job['status'] = 'awaiting'
    answered = job['decision_evt'].wait(timeout=GATE_TIMEOUT)
    with job['lock']:
        job['gate'] = None
        if not answered and job['status'] == 'awaiting':
            job['status'] = 'running'
            job['messages'].append(
                {'ok': False, 'text': 'no answer in %ds — treated as reject'
                                      % GATE_TIMEOUT})
        return bool(job['decision'])


_install_bridge()


# ── in-process memory refresh + background auto-memory scheduler ──
#
# Best-practice "background indexing" model (as IDEs do it): opt-in per
# project, change-detected (skip when nothing changed), single-flight via the
# scan-lock, debounced by a cooldown, incremental persistence. The GUI process
# persists, so refreshes run in-process on a daemon thread (no detached worker
# needed — that's for the TUI, whose process exits on launch).

_sched_started = False
_sched_stop = threading.Event()


#: outcome of the last finished background refresh, per project path. The GUI
#: badge poller watches the scan lock and reads the lock DISAPPEARING as
#: success — so a crashed cycle, which clears the lock in its `finally`, toasted
#: "Memory updated" exactly like a successful one. This is where the difference
#: is recorded. Bounded: one entry per project, replaced each run.
_LAST_REFRESH = {}


def last_refresh(path):
    return _LAST_REFRESH.get(os.path.abspath(path or ''))


def _refresh_project(path, folder, auto_cap=6):
    """Run one incremental memory refresh in-process under the scan-lock so the
    badge and /api/memory/active reflect it. Silent (headless Claude calls).
    Returns True if it actually ran (acquired the lock)."""
    from . import memory
    if not memory.acquire_scan_lock(path):
        return False                      # another refresh already in flight
    memory._tls.silent = True
    key = os.path.abspath(path or '')
    try:
        name = os.path.basename(path.rstrip('\\/')) or path
        # auto_cycle, not refresh_memory: "auto memory" means every memory
        # surface, lessons included. See memory.auto_cycle.
        res = memory.auto_cycle(path, folder, name, auto_cap=auto_cap) or {}
        _LAST_REFRESH[key] = {'ok': True, 'at': time.time(),
                              'extracted': res.get('extracted', 0),
                              'lessons': res.get('lessons', 0),
                              'pending': res.get('pending', 0)}
    except Exception as e:
        _c.log.exception('gui: memory refresh failed for %s', path)
        _LAST_REFRESH[key] = {'ok': False, 'at': time.time(),
                              'error': str(e) or e.__class__.__name__}
    finally:
        memory.clear_scan_lock(path)
    return True


def _refresh_async(path, folder, auto_cap=6):
    """Fire _refresh_project on its own daemon thread (used by the on-open
    autoscan so the HTTP request returns immediately)."""
    import threading
    threading.Thread(target=_refresh_project, args=(path, folder, auto_cap),
                     daemon=True).start()


def _auto_projects():
    """[(path, folder, enc)] for every project opted into auto-memory."""
    from . import gui, memory
    out = []
    for p in gui.list_projects():
        # memory.auto_enabled is the one answer all three runners ask — this
        # loop, the TUI's on-open scan and the detached worker
        if memory.auto_enabled(p['path'], p['encoded']):
            out.append((p['path'],
                        _store.project_folder(p['primary_cfgdir'], p['encoded']),
                        p['encoded']))
    return out


def _auto_scan_pass():
    """One sweep: refresh each opted-in project whose source changed and that
    isn't already updating. Cheap (hash-only) staleness gate keeps token cost
    to genuinely-changed projects.

    Returns True when work is still owed — a cycle hit its per-cycle cap, or a
    project is still stale. That is reported, not acted on: what a pass could
    not finish waits for the next scheduled one (see `_next_wait`).
    """
    from . import memory
    owed = False
    refreshed = 0
    for path, folder, _enc in _auto_projects():
        try:
            if memory.scan_lock_status(path) is not None:
                owed = True                               # still running
                continue
            if not memory.is_stale(path, folder):
                continue                                  # nothing changed
            _refresh_project(path, folder, auto_cap=6)    # blocking, sequential
            refreshed += 1
            if memory.is_stale(path, folder):
                owed = True                               # capped — more to do
        except Exception:
            _c.log.exception('gui: auto-scan pass failed for %s', path)
    # Only when the pass DID something or still owes work. A heartbeat every
    # MIN_INTERVAL would burn the event log's cap inside a day and drown the
    # errors it exists to show.
    if refreshed or owed:
        from . import events
        events.record('scheduler', 'auto-memory pass: %d refreshed%s'
                      % (refreshed, ', more still owed' if owed else ''),
                      level='info')
    return owed


#: how long the first pass waits after start — enough for the server/TUI to
#: settle, short enough that "it updates when I launch claudectl" is true.
#: A module constant so the loop can actually be tested; it had none.
STARTUP_DELAY = 2

#: floor on the configured cadence, and the module seam a test drives the loop
#: through — settings cannot express a sub-minute interval, deliberately.
MIN_INTERVAL = 60


def _next_wait(owed=False):
    """Seconds until the next pass. Always the cadence the user configured.

    It used to return a 45s CATCHUP_INTERVAL whenever a pass reported work still
    owed, on the reasoning that memory should converge rather than sit stale for
    an hour. That inverted the point of the per-cycle cap: a repo with more
    changed modules than one cycle can afford was swept every 45 seconds until
    it caught up, which on several opted-in projects is most of a daily limit
    inside an hour — and on a rate-limited account it was six *failed*
    extractions repeating forever. The cap decides how much one pass may spend;
    the interval decides how often that happens. Nothing else may schedule work.

    `owed` is taken and logged rather than acted on, so "why is my memory still
    stale" has an answer in the log instead of a silent short-circuit.
    """
    from .config import load_settings
    try:
        wait = max(MIN_INTERVAL, int(load_settings().get('auto_memory_interval', 3600)))
    except Exception:
        wait = 3600
    if owed:
        _c.log.info('gui: auto-memory has work left; next pass in %ss '
                    '(the configured interval — a cycle is capped on purpose)', wait)
    return wait


def start_auto_memory_scheduler():
    """Daemon thread: one pass on start, then one every `auto_memory_interval`
    seconds — never sooner, however much work is left. Started by the real entry
    points only (never make_server, so tests don't spawn refreshes). Idempotent."""
    global _sched_started
    if _sched_started:
        return
    _sched_started = True
    import threading
    _sched_stop.clear()

    def _loop():
        # wait(), not sleep(): server_close() must be able to end this, and a
        # thread parked in sleep(3600) cannot be told anything
        if _sched_stop.wait(STARTUP_DELAY):    # let the server/TUI settle first
            return
        while not _sched_stop.is_set():
            owed = False
            try:
                owed = _auto_scan_pass()
            except Exception:
                _c.log.exception('gui: auto-memory scheduler tick failed')
            _sched_stop.wait(_next_wait(owed))

    threading.Thread(target=_loop, daemon=True).start()


def stop_auto_memory_scheduler():
    global _sched_started
    _sched_stop.set()
    _sched_started = False


# ── shared helpers ───────────────────────────────────────────

def _entries():
    """[(mtime, path, enc, cfgdir)] across accounts — same shape main.run
    and the stats screens consume."""
    from .paths import find_actual_path
    out = []
    for _name, acct_dir in _c.all_config_dirs():
        pdir = _store.projects_root(acct_dir)
        if not os.path.isdir(pdir):
            continue
        for enc in os.listdir(pdir):
            proj = os.path.join(pdir, enc)
            if not os.path.isdir(proj):
                continue
            actual = find_actual_path(enc, folder=proj)
            if actual:
                out.append((os.path.getmtime(proj), actual, enc, acct_dir))
    out.sort(reverse=True)
    return out


def _folder(cfgdir, enc):
    """The project folder for a request-supplied `enc`. Raises ValueError when
    `enc` is not something `paths.encode_component` could have produced — it
    reaches roughly forty endpoints straight off the wire."""
    return _store.project_folder(cfgdir, enc)


class BadRequest(ValueError):
    """A request the caller got wrong. 400, never 500."""


def _cfgdir_ok(v):
    """An account directory claudectl actually knows about.

    Unvalidated, this parameter is a filesystem read primitive: it is joined
    with 'projects' and a name on about forty endpoints, so any directory on
    the machine could be walked by asking for it.
    """
    want = os.path.normcase(os.path.abspath(v))
    return any(os.path.normcase(os.path.abspath(d)) == want
               for _n, d in _c.all_config_dirs())


def _managed_path_ok(v):
    """Is this somewhere claudectl is allowed to DELETE?

    `os.remove(body['file'])` and `shutil.rmtree(body['dir'])` were reachable
    with any path at all, so `{"dir": "C:\\\\Users\\\\mab"}` was a recursive
    delete of the home directory. The token gates it, but a guard that only
    holds while a secret does is one layer, not two.

    Enumerating every root is the wrong shape — project-scoped agents and skills
    live under an arbitrary project. Both managed locations are recognisable
    instead: an account config directory (which `_cfgdir_ok` already knows), or
    anything below a `.claude` / `.claudectl` directory, which is where every
    project-scoped one lives by construction.
    """
    p = os.path.normcase(os.path.abspath(v))
    for _n, d in _c.all_config_dirs():
        root = os.path.normcase(os.path.abspath(d))
        if p == root or p.startswith(root + os.sep):
            return True
    parts = p.split(os.sep)
    return '.claude' in parts or '.claudectl' in parts


#: checked by NAME, wherever they appear. These reach the filesystem; `path` is
#: deliberately absent, because several endpoints legitimately take a directory
#: that does not exist yet, and every path that becomes a subprocess cwd already
#: goes through paths.resolve_dir.
#:
#: `target_cfgdir` is here because naming a parameter differently was enough to
#: skip the check entirely: `api_inject_launch` put its value straight into
#: CLAUDE_CONFIG_DIR for a spawned `claude`, on the one endpoint whose docstring
#: says it is validated now. `dir` is deliberately NOT here — `accounts/add`
#: legitimately names a directory that does not exist yet — so the destructive
#: `dir` sinks call `_managed_path_ok` themselves.
PARAM_CHECKS = {
    'enc': lambda v: _store.is_encoded(v),
    'sid': lambda v: _store.is_encoded(v),
    'cfgdir': _cfgdir_ok,
    'target_cfgdir': _cfgdir_ok,
}


def check_params(params):
    """Raise BadRequest for any known parameter present but malformed."""
    if not isinstance(params, dict):
        return
    for name, ok in PARAM_CHECKS.items():
        v = params.get(name)
        if v in (None, ''):
            continue
        if not isinstance(v, str) or not ok(v):
            raise BadRequest('invalid %s' % name)


def call(fn, q=None, body=None):
    """Run one endpoint with its parameters checked, translating the two ways a
    caller can be wrong into 400s.

    A missing parameter used to surface as a KeyError caught by the generic
    handler and returned as 500 {"error": "'enc'"} — indistinguishable, to the
    SPA, from the server breaking.
    """
    check_params(q)
    check_params(body)
    try:
        return fn(q or {}, body)
    except KeyError as e:
        # Only a KeyError naming a REQUEST parameter is the caller's fault; an
        # internal one is still a 500 and still gets logged as such.
        key = e.args[0] if e.args else ''
        if key in _REQUEST_PARAMS:
            raise BadRequest('missing parameter: %s' % key)
        raise


#: Names that only ever come off the wire, so a KeyError naming one is the
#: caller's fault. Kept explicit rather than "any KeyError": an internal one is
#: a real fault and must stay a 500. The list was short by five, which the
#: endpoint floor found as five separate 500s.
_REQUEST_PARAMS = frozenset((
    'enc', 'sid', 'cfgdir', 'target_cfgdir', 'path', 'action', 'kind', 'name',
    'id', 'dir', 'file', 'key', 'event', 'scope', 'text', 'value', 'model',
    'task', 'url', 'query', 'q', 'ts',
))


# ── sessions & transcript ────────────────────────────────────

#: a transcript here routinely passes 100 MB; the drawer pages instead of
#: serialising every message of a 2,787-message session into one response
_TRANSCRIPT_PAGE = 400


def api_transcript(q, body):
    from .transcript import page
    jsonl = _store.session_file(q.get('cfgdir'), q['enc'], q['sid'])
    return page(jsonl, max(0, int(q.get('offset') or 0)),
                min(_TRANSCRIPT_PAGE, max(1, int(q.get('limit') or _TRANSCRIPT_PAGE))))


def api_session_meta(q, body):
    from .transcript import metadata_lines
    from .stats import get_session_stats_cached
    from .sessions import load_name
    folder = _folder(q.get('cfgdir'), q['enc'])
    jsonl = os.path.join(folder, f"{q['sid']}.jsonl")
    stats = get_session_stats_cached(jsonl)
    return {'lines': metadata_lines(stats, load_name(folder, q['sid']),
                                    q['sid'], plain=True)}


def api_session_export(q, body):
    from .transcript import export_transcript
    ok, msg = export_transcript(_folder(body.get('cfgdir'), body['enc']),
                                body['sid'], body['path'])
    return {'ok': ok, 'message': msg}


def api_changed_files(q, body):
    from .sessions import session_changed_files
    jsonl = os.path.join(_folder(q.get('cfgdir'), q['enc']), f"{q['sid']}.jsonl")
    return {'files': session_changed_files(jsonl)}


def api_session_archive(q, body):
    from .session_menu import _move_session, _arch_of
    folder = _folder(body.get('cfgdir'), body['enc'])
    errs = _move_session(folder, _arch_of(folder), body['sid'])
    return {'ok': not errs, 'errors': errs}


def api_session_restore(q, body):
    from .session_menu import _move_session, _arch_of
    folder = _folder(body.get('cfgdir'), body['enc'])
    errs = _move_session(_arch_of(folder), folder, body['sid'])
    return {'ok': not errs, 'errors': errs}


def api_session_delete(q, body):
    from .session_menu import _delete_session, _arch_of
    folder = _folder(body.get('cfgdir'), body['enc'])
    if body.get('archived'):
        folder = _arch_of(folder)
    errs = _delete_session(folder, body['sid'])
    return {'ok': not errs, 'errors': errs}


def api_archived(q, body):
    """Archived sessions of a project across EVERY account, newest-first.

    This used to resolve exactly one folder — whatever `cfgdir` the client sent,
    which was always `CUR.primary_cfgdir`, i.e. the first account in
    `all_config_dirs()` order that has the project (so `default` whenever it has
    it). Anything archived under another account was invisible, and the rows
    carried no `account`/`cfgdir` either, so even a visible one could not have
    been restored to the right place.

    The archive is per-account and per-project (`<cfgdir>/projects/<enc>/archived`),
    so there is nothing global to read — the walk is the fix. Mirrors
    `gui.list_sessions` rather than inventing a second shape, and the TUI's
    archived tab (`session_menu._rescan_archived`) has always merged accounts
    this way.
    """
    from .session_menu import _arch_of
    from .sessions import (account_folders_for, scan_sessions, load_name,
                           format_age)
    from .stats import get_session_stats_cached
    from .gui import _used_omni
    out = []
    for acct_name, folder in account_folders_for(q['enc']):
        arch = _arch_of(folder)
        cfgdir = os.path.dirname(os.path.dirname(folder))
        for mtime, sid, preview, count in scan_sessions(arch):
            omni, ai_title = False, ''
            try:
                st = get_session_stats_cached(os.path.join(arch, f'{sid}.jsonl'))
                omni = _used_omni(st)
                # the AI title, same as a live row. It was already parsed — the
                # stats dict is being read here anyway — and without it every
                # archived row fell back to the preview.
                ai_title = st.get('title') or ''
            except Exception:
                pass
            out.append({'sid': sid, 'title': load_name(arch, sid) or ai_title,
                        'preview': preview, 'age': format_age(mtime).strip(),
                        'mtime': mtime, 'count': count, 'account': acct_name,
                        'cfgdir': cfgdir, 'omni': omni})
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return {'sessions': out}


def api_tags_get(q, body):
    from .sessions import load_tags
    return {'tags': load_tags(_folder(q.get('cfgdir'), q['enc']))}


def api_tags_set(q, body):
    from .sessions import load_tags, save_tags
    folder = _folder(body.get('cfgdir'), body['enc'])
    tags = load_tags(folder)
    tags[body['sid']] = body.get('tags', [])
    if not tags[body['sid']]:
        tags.pop(body['sid'], None)
    save_tags(folder, tags)
    return {'ok': True}


# ── usage & search ───────────────────────────────────────────

def api_usage_daily(q, body):
    from .stats import usage_by_day, fmt_tok
    rows = []
    for day, usage, cost, n_sessions in usage_by_day(
            _entries(), days=int(q.get('days', 14)), silent=True):
        tot = sum(usage.values())
        rows.append({'day': day, 'tokens': tot, 'tok_fmt': fmt_tok(tot),
                     'cost': round(cost, 2), 'sessions': n_sessions,
                     'usage': usage})
    return {'days': rows}


def api_usage_projects(q, body):
    from .stats import assemble_project_usage
    return {'projects': assemble_project_usage(_entries())}


def api_usage_project(q, body):
    from .stats import assemble_session_usage
    return {'sessions': assemble_session_usage(_folder(q.get('cfgdir'), q['enc']))}


def api_usage_plan(q, body):
    from . import usage as usage_mod
    if q.get('refresh'):
        # one network round-trip per account, so never inline: it would hold the
        # request thread (and the clicked button) for as long as the slowest or
        # most-expired account takes. The client short-polls for the result.
        threading.Thread(target=usage_mod.refresh_now, daemon=True).start()
    else:
        usage_mod._ensure_started()
    out = []
    with usage_mod._lock:
        state = dict(usage_mod._acct_state)
    # every configured account must appear even before async data lands
    names = {d: n for n, d in usage_mod._targets()}
    now = time.time()

    def row(d, st, name):
        data = st.get('data')
        wins = usage_mod._extract_windows(data) if data else []
        status = st.get('status') or 'pending'
        fetched = st.get('fetched_at') or 0
        return {'account': st.get('name') or name, 'email': st.get('email', ''),
                'dir': d, 'status': status,
                'status_text': usage_mod.STATUS_TEXT.get(status, status),
                'stale_secs': int(now - fetched) if fetched else None,
                'retry_in': max(0, int(st.get('retry_at', 0) - now)) or None,
                'plan': st.get('plan', ''), 'tier': st.get('tier', ''),
                'spend': st.get('spend'),
                'windows': [{'label': l, 'pct': p,
                             'resets': usage_mod._fmt_reset(r) if r else ''}
                            for l, p, r in wins]}

    for d, st in state.items():
        out.append(row(d, st, names.get(d, os.path.basename(d))))
    for d, name in names.items():
        if d not in state:
            out.append(row(d, {}, name))
    return {'accounts': out}


def api_search_index(q, body):
    from .search import build_search_index
    rows, partial = build_search_index(_entries())
    return {'rows': rows, 'partial': partial}


# ── home dashboard ───────────────────────────────────────────

_DASH_TTL = 10
_DASH_DAYS = 30          # one scan feeds every chart range the UI offers (7/14/30)
_dash_cache = None
_dash_cached_at = 0.0


def _wiring():
    """Is this workspace actually set up — per account, from files only.

    Everything here is a settings.json read (small, and already warm because
    hooks._load is the one reader for that file). No subprocess: this rides the
    dashboard poll, and the MCP probe next to it is already the expensive part
    of that payload.
    """
    from . import hooks
    from . import automode
    rows = []
    for name, d in _c.all_config_dirs():
        try:
            s = hooks._load(d)
        except Exception:
            s = {}
        n_hooks = sum(len(v) for v in (s.get('hooks') or {}).values()
                      if isinstance(v, list))
        sl = bool(s.get('statusLine'))
        rows.append({
            'account': name, 'dir': d,
            'hooks': n_hooks,
            'statusline': sl,
            # a statusline the classic renderer will never draw is installed and
            # invisible, which looks identical to working from the settings file
            'statusline_hidden': sl and s.get('tui') != 'fullscreen',
            'mode': automode.default_mode(d) or '',
        })
    ok = sum(1 for r in rows
             if r['hooks'] and r['statusline'] and not r['statusline_hidden'])
    return {'accounts': rows, 'ok': ok, 'total': len(rows)}


def _last_message(st):
    msgs = st.get('messages') or []
    last = msgs[-1] if msgs else ''
    return (last.get('text', '') if isinstance(last, dict) else str(last))[:160]


def api_dashboard(q, body):
    """Home-screen aggregate: today/week usage, live jobs, MCP status,
    cross-account recent sessions, failover proxy state. Cached _DASH_TTL
    seconds so MCP status (subprocess-spawning) isn't recomputed every poll."""
    global _dash_cache, _dash_cached_at
    now = time.monotonic()
    if _dash_cache is not None and now - _dash_cached_at < _DASH_TTL:
        return _dash_cache
    from datetime import datetime
    from .stats import assemble_breakdown
    from .sessions import load_name, format_age
    from . import mcp as mcp_mod
    from . import failover as fov

    bd = assemble_breakdown(_entries(), days=_DASH_DAYS)
    week = bd['days'][-7:]                         # exactly 7, oldest→newest
    tkey = datetime.now().strftime('%Y-%m-%d')
    tday = next((d for d in bd['days'] if d['date'] == tkey), None) or {}
    tk, tc, ts = tday.get('tokens', 0), tday.get('cost', 0.0), tday.get('sessions', 0)

    # Every job, terminal ones included. The dashboard used to send them all and
    # the client kept only the running count, so "what did that memory build
    # actually do" had no surface anywhere — the Activity drawer is that surface.
    # _reap_locked already bounds the registry, so this cannot grow unbounded.
    with _JOBS_LOCK:
        items = list(_JOBS.items())
    jobs = []
    for jid, _jd in items:
        st = job_status(jid)
        if not st:
            continue
        jobs.append({'id': st['id'], 'kind': st.get('label', ''),
                     'status': st.get('status', ''),
                     'elapsed': st.get('elapsed', 0),
                     'started': st.get('started', 0),
                     'ended': st.get('ended', 0),
                     'error': (st.get('error') or '')[:200],
                     # last line of chatter: what it was doing when it stopped.
                     # Every producer appends {'ok','text'}, so indexing the
                     # entry as a string raised TypeError the moment a job had
                     # said anything at all.
                     'last': _last_message(st)})
    jobs.sort(key=lambda j: (j['status'] not in ('awaiting', 'running'),
                             -(j['ended'] or j['started'])))

    mcp_rows = [{'name': n, 'running': s == 'ok'}
                for n, s in mcp_mod.get_mcp_status()]

    # from the breakdown's own scan of every account's transcripts, not the
    # last-session.json launch history — that only records sessions claudectl
    # itself started, so anything opened by `claude` directly never showed up.
    recent = []
    for r in bd.get('recent', []):
        pf = _store.project_folder(r['cfgdir'], r['encoded'])
        recent.append({'id': r['sid'], 'sid': r['sid'], 'path': r['path'],
                       'encoded': r['encoded'], 'cfgdir': r['cfgdir'],
                       'project': os.path.basename(r['path']) or r['path'],
                       'account': r.get('account', ''),
                       'title': load_name(pf, r['sid']) or r['title'],
                       'msgs': r['msgs'], 'mtime': int(r['mtime']),
                       'age': format_age(r['mtime']).strip() if r['mtime'] else '',
                       'omni': bool(r['omni'])})

    f_running, f_port = False, None
    try:
        lock = fov._read_lock()
        if lock and fov._pid_alive(lock.get('pid')):
            f_running, f_port = True, lock.get('port')
    except Exception:
        pass

    _dash_cache = {'today': {'tokens': tk, 'cost': tc, 'sessions': ts,
                             # {account: tokens} for TODAY — the one aggregate
                             # across accounts that is genuinely additive, and
                             # therefore the only honest thing to stack in a
                             # single ring. Percentages of five separate quotas
                             # are not summable and must never be added up.
                             'by_account': dict(tday.get('accounts') or {}),
                             'omni_tokens': tday.get('omni_tokens', 0)},
                   'wiring': _wiring(),
                   'week': week, 'breakdown': bd, 'days': _DASH_DAYS,
                   # cross-account live sessions + the last 24 HOURS of activity.
                   # Hoisted out of the breakdown so the Activity card does not
                   # have to know where in the payload they live.
                   'live': bd.get('live') or {'total': 0, 'by_account': {}},
                   'hours': bd.get('hours') or [],
                   'jobs': jobs, 'mcp': mcp_rows,
                   'recent': recent,
                   'failover': {'running': f_running, 'port': f_port},
                   'generated_at': int(time.time())}
    _dash_cached_at = time.monotonic()
    return _dash_cache


# ── ambient motion feed ──────────────────────────────────────

_GLITE_TTL = 60
_glite_cache = {}


def api_graph_lite(q, body):
    """Compact project shape for the ambient motion layer.

    Deliberately NOT the /graph payload: the animated themes need a couple of
    dozen numbers per frame-bind, not the full node/edge graph the standalone
    visualiser renders. Reuses connections.build_hierarchy, which is already
    signature-cached on disk, so a repeat call costs a directory walk at worst
    — and this endpoint caches on top of that for _GLITE_TTL seconds because
    the motion layer polls it while a project page is open.
    """
    from . import connections
    path = q.get('path', '')
    enc = q.get('enc', '')
    cfgdir = q.get('cfgdir') or _c.config_dir
    refresh = q.get('refresh') in ('1', 'true', 'yes')
    key = (path, enc, cfgdir)
    now = time.monotonic()
    hit = _glite_cache.get(key)
    if hit and not refresh and now - hit[0] < _GLITE_TTL:
        return hit[1]

    proj_folder = _folder(cfgdir, enc) if enc else None
    try:
        g = connections.build_hierarchy(path, proj_folder, force=refresh)
    except Exception:
        g = {'nodes': [], 'dep_edges': [], 'meta': {}}

    # top-level modules only — one animated element per module keeps the field
    # readable; deeper nodes would just be noise at ambient opacity
    mods = [n for n in g.get('nodes', [])
            if n.get('depth') == 1 and n.get('type') in ('dir', 'repo')]
    mods.sort(key=lambda n: n.get('total_files', 0), reverse=True)
    mods = mods[:24]
    idx = {n['id']: i for i, n in enumerate(mods)}

    now_s = time.time()
    modules = []
    for n in mods:
        mt = 0.0
        try:
            mt = os.path.getmtime(os.path.join(path, n.get('label', '')))
        except OSError:
            pass
        # 0..1 recency ramp over a week, same shape as the dashboard's node heat
        heat = max(0.0, min(1.0, 1 - (now_s - mt) / (3600 * 24 * 7))) if mt else 0.0
        modules.append({'label': n.get('label', ''),
                        'files': n.get('total_files', 0),
                        'rank': n.get('rank', 0),
                        'heat': round(heat, 3)})

    # dependency edges are aggregated at whatever directory level the import
    # crossed, so roll each endpoint up to its top-level module before pairing
    # — otherwise every edge sits below depth 1 and the field draws nothing
    def _rollup(nid):
        # endpoints are file: ids far more often than dir: ids; a root-level
        # file belongs to no module and drops out
        rel = (nid or '').split(':', 1)[-1] if nid else ''
        head, sep, _ = rel.partition('/')
        return ('dir:' + head) if sep else None

    agg = {}
    for e in g.get('dep_edges', []):
        a, b = idx.get(_rollup(e.get('source'))), idx.get(_rollup(e.get('target')))
        if a is not None and b is not None and a != b:
            agg[(a, b)] = agg.get((a, b), 0) + e.get('weight', 1)
    edges = sorted(([a, b, w] for (a, b), w in agg.items()),
                   key=lambda r: r[2], reverse=True)[:60]
    for a, b, w in edges:                 # module weight = its edge traffic
        modules[a]['rank'] += w
        modules[b]['rank'] += w

    mem = {}
    try:
        from .memhub import _state
        from .lessons import pending_sids
        st = _state(path, proj_folder) if proj_folder else None
        if st:
            m = st['mem']
            try:
                unscanned = len(pending_sids(proj_folder, m))
            except Exception:
                unscanned = 0
            mem = {'entities': len(st['entities']), 'lessons': len(st['lessons']),
                   'pending': len(st['pending']), 'unscanned': unscanned,
                   'generated_at': m.get('generated_at', '')}
    except Exception:
        pass

    counts = (g.get('meta') or {}).get('counts') or {}
    out = {'modules': modules, 'edges': edges, 'memory': mem,
           'files': counts.get('files', 0), 'repos': counts.get('repos', 0),
           # dirs/deps/truncated and the top repos come from the same dict the
           # motion feed already had in hand — the TUI architecture screen shows
           # them and the GUI showed none of them, with this whole endpoint
           # having no consumer at all.
           'dirs': counts.get('dirs', 0), 'deps': counts.get('deps', 0),
           'truncated': bool((g.get('meta') or {}).get('truncated')),
           'top_repos': [{'label': n.get('label', ''),
                          'files': n.get('total_files', 0), 'deps': n.get('rank', 0)}
                         for n in connections.top_repos(g, 8)],
           'languages': (g.get('meta') or {}).get('languages') or {},
           'generated_at': int(now_s)}
    _glite_cache[key] = (now, out)
    if len(_glite_cache) > 24:            # bounded: one entry per project visited
        for k in list(_glite_cache)[:-24]:
            _glite_cache.pop(k, None)
    return out


# ── managers: hooks / agents / mcp / accounts ────────────────

def api_hooks_get(q, body):
    """Every hook in ONE account, enabled and disabled alike.

    Walking only `hooks` made a hook disabled in the TUI vanish here — and then
    offered its template as uninstalled, so the GUI would happily create an
    enabled duplicate beside the disabled one.
    """
    from . import hooks
    cfgdir = q.get('cfgdir')
    d = hooks._load(cfgdir)
    out = []
    for key in hooks.HOOK_STATE_KEYS:
        for event, block in (d.get(key) or {}).items():
            for i, entry in enumerate(block if isinstance(block, list) else []):
                out.append({'event': event, 'index': i,
                            'enabled': key == 'hooks',
                            'label': hooks._hook_label(entry, event),
                            'matcher': entry.get('matcher', '')})
    # per-account view, the shape statusline.per_account() already uses for
    # exactly this question: which accounts are missing what.
    per = [(n, dd, hooks._load(dd)) for n, dd in hooks.account_dirs()]
    return {'hooks': out,
            'settings_path': hooks.settings_path_for(cfgdir),
            # the plain-English phrase for every event, so the screen can lead
            # with "before Claude runs a tool" instead of `PreToolUse`
            'events': hooks.EVENT_WHEN,
            'accounts': [{'name': n, 'dir': dd,
                          'count': sum(len(b or []) for b in (a.get('hooks') or {}).values())}
                         for n, dd, a in per],
            # `event` rides along so the template list can be grouped by WHEN a
            # hook fires, the same way the installed list is
            'templates': [{'key': k, 'desc': v.get('desc', ''),
                           'event': v.get('event', ''),
                           'installed': _template_installed(hooks, d, v),
                           'missing': [n for n, _dd, a in per
                                       if not _template_installed(hooks, a, v)]}
                          for k, v in hooks.TEMPLATES.items()]}


def _template_installed(hooks, d, tpl):
    """True when this template's hook exists in EITHER state block."""
    want = hooks._cmd_keys(tpl['entry'])
    return any(hooks._cmd_keys(e) == want
               for key in hooks.HOOK_STATE_KEYS
               for e in ((d.get(key) or {}).get(tpl['event']) or []))


def api_hooks_template(q, body):
    from . import hooks
    t = hooks.TEMPLATES.get(body['key'])
    if not t:
        return {'ok': False, 'error': 'unknown template'}
    targets = _hook_targets(hooks, body)
    done = []
    for name, cfgdir in targets:
        d = hooks._load(cfgdir)
        # Installing the same template twice is never what anyone means, and it
        # is exactly what a row stuck on "Install" invites you to do. Two copies
        # of a guard fire the same block twice; two copies of a memory hook
        # inject the context twice. The disabled block counts too — otherwise a
        # hook the user turned OFF is silently re-added, enabled, beside it.
        if _template_installed(hooks, d, t):
            continue
        d.setdefault('hooks', {}).setdefault(t['event'], []).append(t['entry'])
        hooks._save(d, cfgdir)
        done.append(name)
    if not done:
        return {'ok': True, 'already': True,
                'message': f'{body["key"]} is already installed'}
    return {'ok': True, 'accounts': done}


def api_hooks_toggle(q, body):
    """Enable or disable one hook, without deleting it."""
    from . import hooks
    ok = hooks.set_hook_enabled(body['event'], body['index'],
                                bool(body.get('enabled')), body.get('cfgdir'))
    return {'ok': ok, 'error': '' if ok else 'not found'}


def api_hooks_remove(q, body):
    from . import hooks
    ok = hooks.remove_hook(body['event'], body['index'],
                           body.get('enabled', True) is not False,
                           body.get('cfgdir'))
    return {'ok': ok, 'error': '' if ok else 'not found'}


def api_hooks_purge(q, body):
    from . import hooks
    removed = 0
    for _name, cfgdir in _hook_targets(hooks, body):
        d = hooks._load(cfgdir)
        for key in hooks.HOOK_STATE_KEYS:
            for event in list((d.get(key) or {})):
                block = d[key][event]
                keep = [e for e in block
                        if not any(hooks._is_broken(c) for c in hooks._entry_commands(e))]
                removed += len(block) - len(keep)
                if keep:
                    d[key][event] = keep
                else:
                    d[key].pop(event)
        hooks._save(d, cfgdir)
    return {'ok': True, 'removed': removed}


def _hook_targets(hooks, body):
    """Which accounts an install/purge acts on. Default: ALL of them.

    What the user provisions is a property of THEM, not of whichever account
    happened to be active — measured across five accounts, the default one had
    18 hooks and the other four had none.
    """
    if body.get('cfgdir'):
        return [(body.get('account') or body['cfgdir'], body['cfgdir'])]
    if body.get('scope') == 'active':
        return [('active', None)]
    return hooks.account_dirs()


def api_agents_library(q, body):
    from .agents import (list_categories, list_library_agents,
                         list_agents, user_agents_dir, project_agents_dir)
    cats = []
    for c in list_categories():
        agents = [{'name': name, 'model': model, 'path': path,
                   'desc': (desc or '')[:140]}
                  for name, desc, model, path in list_library_agents(c)]
        cats.append({'category': c, 'agents': agents})
    # `own` used to be the active account's user agents plus, only when a
    # `path` was supplied, that one project's. The global Agents page has no
    # path, so it listed a fraction of what is installed — and the page that
    # now offers a machine-wide Sharpen has to show what it is about to touch.
    from .agents import all_installed, category_of, installed_categories
    mine = []
    if q.get('path'):
        for scope, d in (('user', user_agents_dir()),
                         ('project', project_agents_dir(q['path']))):
            for n, desc, model, path in list_agents(d):
                mine.append({'name': n, 'desc': (desc or '')[:140], 'model': model,
                             'path': path, 'scope': scope,
                             'category': category_of(path)})
    else:
        for r in all_installed():
            if r['scope'] == 'library':
                continue          # already listed below, by category
            mine.append({'name': r['name'], 'desc': r['desc'][:140], 'model': '',
                         'path': r['path'], 'category': r['category'],
                         'scope': (r['scope'] if not r['project_path'] else
                                   'project · ' + os.path.basename(r['project_path']))})
    from .agents import KNOWN_TOOLS
    from .config import models
    vals, labels = models()
    return {'categories': cats, 'own': mine,
            # every category name you can file a new agent under: the library's
            # folders plus the ones your own agents already use, so the second
            # agent in an invented category joins it instead of starting
            # "Reviewers" beside "reviewers"
            'category_names': sorted(set(list_categories())
                                     | set(installed_categories())),
            # api_agent_create already accepted `tools` and `model`; the form
            # simply never had anything to offer for them.
            'known_tools': list(KNOWN_TOOLS),
            'models': [{'id': v, 'label': l} for v, l in zip(vals, labels)]}


def api_agent_read(q, body):
    from .agents import parse_agent
    meta, body_txt = parse_agent(q['file'])
    return {'meta': meta, 'body': body_txt}


def api_agent_create(q, body):
    from .agents import write_agent, user_agents_dir, project_agents_dir, _slug
    d = project_agents_dir(body['path']) if body.get('scope') == 'project' else user_agents_dir()
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{_slug(body['name'])}.md")
    # `category` is claudectl's own frontmatter key — Claude Code ignores what
    # it does not recognise, and `write_agent` preserves any key not in its
    # fixed order, so filing an agent costs nothing at load time.
    cat = (body.get('category') or '').strip()
    write_agent(p, {'name': body['name'],
                    'description': body.get('description', ''),
                    **({'tools': body['tools']} if body.get('tools') else {}),
                    **({'model': body['model']} if body.get('model') else {}),
                    **({'category': cat} if cat else {})},
                body.get('body', ''))
    return {'ok': True, 'file': p}


def api_agent_delete(q, body):
    f = body['file']
    if not f.lower().endswith('.md') or not _managed_path_ok(f):
        raise BadRequest('not an agent file claudectl manages')
    try:
        os.remove(f)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def api_agents_session_get(q, body):
    from .sessions import load_session_agents
    from .agents import suggest_agents, SAFE_AGENT_LIMIT
    folder = _folder(q.get('cfgdir'), q['enc'])
    suggested = []
    if q.get('path'):
        try:
            suggested = [{'ref': r, 'reason': reason}
                         for r, reason, _s in suggest_agents(q['path'], folder)]
        except Exception:
            pass
    from .agents import usage as _agent_usage, routing_table
    return {'refs': load_session_agents(folder).get('__project__', []),
            'suggested': suggested, 'limit': SAFE_AGENT_LIMIT,
            # what Claude Code says it has actually delegated to, and the
            # delegation table claudectl writes into CLAUDE.md so it can
            'usage': _agent_usage(q.get('cfgdir')),
            'routing': [{'name': n, 'trigger': t}
                        for n, t in (routing_table(q['path']) if q.get('path') else [])]}


def api_agents_session(q, body):
    from .agents import sync_project_agents
    from .sessions import save_session_agents
    refs = body.get('refs', [])
    folder = _folder(body.get('cfgdir'), body['enc'])
    if os.path.isdir(folder):
        save_session_agents(folder, '__project__', refs)
    n = sync_project_agents(body['path'], refs)
    from .agents import routing_table
    return {'ok': True, 'active': n, 'routed': len(routing_table(body['path']))}


def api_health(q, body):
    """Project health — 229 lines of checks that were a README headline and had
    no GUI trace at all. Also the permission-allowlist proposal, which is
    derived from what this project's transcripts actually ran."""
    from . import health
    folder = _folder(q.get('cfgdir'), q['enc']) if q.get('enc') else None
    issues = health.check_project(q['path'], folder)
    return {'issues': [{'severity': s, 'message': m, 'hint': h}
                       for s, m, h in issues],
            'bash': [{'command': c, 'count': n}
                     for c, n in health.frequent_bash_commands(folder)]}


def api_health_allowlist(q, body):
    from . import health
    folder = _folder(body.get('cfgdir'), body['enc']) if body.get('enc') else None
    n, err = health.propose_allowlist(body['path'], folder)
    if err:
        raise BadRequest(err)
    return {'ok': True, 'added': n}


def api_brief(q, body):
    """What to work on, and what changed since the last session."""
    from . import brief
    folder = _folder(q.get('cfgdir'), q['enc']) if q.get('enc') else None
    # cached by default — this walks every repo in the project with two git
    # calls each, and the Tools tab used to pay that on every visit
    diff = brief.session_diff_rows(q['path'], folder,
                                   refresh=q.get('refresh') in ('1', 'true'))
    from . import memory as _mem
    return {'suggestions': [{'tag': t, 'text': x}
                            for t, x in brief.work_suggestions(q['path'], folder)],
            # so the card can say how old the AI half of the advice is rather
            # than presenting a month-old finding as current
            'scan_at': brief.scan_age(_mem.load_memory(q['path'], folder)),
            # structured, so the GUI can collapse per repo and show counts.
            # `since_last` stays for any client still reading the flat lines.
            'since': diff,
            'since_last': brief.session_diff(q['path'], folder)}


def api_brief_dismiss(q, body):
    """Stop showing one scan finding. Remembered across re-scans."""
    from . import brief
    n = brief.dismiss_scan_item(body['path'],
                                _folder(body.get('cfgdir'), body['enc']),
                                body.get('text', ''))
    return {'ok': True, 'dismissed': n}


def api_conventions(q, body):
    """Conventions shared across projects, and the global CLAUDE.md block they
    would become — plus the candidates that did not qualify, so an empty card
    can say why it is empty instead of only that it is."""
    from . import conventions
    return {'conventions': conventions.collect_conventions(),
            'near': conventions.near_misses(),
            'block': conventions.build_block()}


def api_conventions_pin(q, body):
    from . import conventions
    n = conventions.pin_convention((body or {}).get('text', ''))
    return {'ok': bool(n), 'pinned': n}


def api_conventions_sync(q, body):
    from . import conventions
    ok = conventions.sync_to_global((body or {}).get('cfgdir'))
    return {'ok': bool(ok)}


# ── Claude Code's own client state ───────────────────────────

def api_client_usage(q, body):
    """What is actually being used versus carried as dead weight."""
    from . import clientstate
    return clientstate.usage_rollup(q.get('cfgdir') or None)


def api_client_project(q, body):
    """Claude Code's own record for one project: cost, tokens, MCP approval
    state, allowed tools. Distinct from claudectl's own stats, which are
    derived from transcripts."""
    from . import clientstate
    st = clientstate.project_state(q['path'], q.get('cfgdir') or None)
    return {'state': st, 'known': bool(st)}


def api_prompt_history(q, body):
    from . import clientstate
    return {'prompts': clientstate.prompt_history(
        q.get('q', ''), int(q.get('limit') or 200), q.get('cfgdir') or None)}


def api_background_agents(q, body):
    from . import clientstate
    return {'daemon': clientstate.daemon_roster(q.get('cfgdir') or None),
            'teams': clientstate.teams(q.get('cfgdir') or None)}


def api_logs(q, body):
    """claudectl's own event log — what it did and what failed, newest first."""
    from . import events
    return {'events': events.read(), 'path': events.path(),
            'cap': events.MAX_BYTES, 'debug_log': _c.log_file_path()}


def api_disk(q, body):
    from . import clientstate
    return clientstate.disk_report()


def api_disk_gc(q, body):
    from . import diskgc
    return diskgc.run(days=int(body.get('days') or 0),
                      apply=bool(body.get('apply')),
                      cfgdir=body.get('cfgdir') or None)


def api_worklog_get(q, body):
    from .config import load_settings
    from .worklog import load_worklog
    from . import hooks
    enc = q.get('enc', '')
    on = bool((( load_settings().get('project_defaults') or {}).get(enc) or {}).get('worklog'))
    entries = load_worklog(q['path']) if q.get('path') else []
    # across_accounts, like the POST beside it. Reading one account made the
    # toggle look installed while the account actually running the session had
    # no hook — the same import-frozen-account class of bug, one call short.
    return {'on': on,
            'installed': hooks.across_accounts(hooks.worklog_hook_installed),
            'entries': list(reversed(entries))[:10]}


def api_worklog_set(q, body):
    """Per-project recent-work memory on/off."""
    from . import hooks, memhub
    on = memhub.set_worklog(body.get('enc', ''), body.get('on'))
    return {'ok': True, 'on': on,
            'installed': hooks.across_accounts(hooks.worklog_hook_installed)}


def api_memory_toggles(q, body):
    """The two memory flags the GUI could only print, plus the recall budget."""
    from .config import load_settings, save_settings
    from . import hooks, memhub
    enc = body.get('enc', '')
    if 'hook' in body:
        memhub.set_prompt_hook(enc, body['hook'])
    if 'rules' in body:
        memhub.set_memory_rules(body['rules'], body.get('path'),
                                _folder(body.get('cfgdir'), enc) if enc else None)
    if 'budget' in body:
        s = load_settings()
        s['memory_budget'] = max(0, min(20000, int(body['budget'] or 0)))
        save_settings(s)
    s = load_settings()
    proj = (s.get('project_defaults') or {}).get(enc) or {}
    return {'ok': True,
            'hook_on': bool(proj.get('memory_hook', s.get('memory_prompt_hook', False))),
            'rules_on': bool(s.get('memory_rules', True)),
            'budget': s.get('memory_budget', 600),
            'installed': hooks.across_accounts(hooks.memory_hook_installed)}


def api_skills_get(q, body):
    """Every skill Claude Code can load, by scope, with its real usage.

    The old payload was `{templates, project}` — a list of starters plus one
    project's folder, which said nothing about what is actually active."""
    from .skills import inventory
    inv = inventory(q.get('path') or '', q.get('cfgdir'))
    for rows in ('personal', 'project', 'plugin', 'bundled', 'templates'):
        for r in inv[rows]:
            r['desc'] = (r.get('desc') or '')[:160]
    inv['accounts'] = [{'name': n, 'dir': d} for n, d in _c.all_config_dirs()]
    return inv


def api_skill_read(q, body):
    from .skills import parse_skill
    meta, body_txt = parse_skill(q['dir'])
    return {'meta': meta, 'body': body_txt}


def _skill_dest(body):
    """Where an install or a create lands. `scope` is explicit because there
    are two real answers and defaulting to the project would put a skill where
    it only works in one place."""
    from .skills import personal_dir, project_skills_dir
    if body.get('scope') == 'project':
        if not body.get('path'):
            raise BadRequest('scope=project needs a path')
        return project_skills_dir(body['path'])
    return personal_dir(body.get('cfgdir'))


def api_skill_install(q, body):
    """Install into the project, or into the personal scope of every account —
    see skills.install_personal for why personal means all of them."""
    from .skills import install_skill, install_personal
    if body.get('scope') != 'project':
        done = install_personal(body.get('dir', ''))
        return {'ok': bool(done), 'dir': done[0][1] if done else '',
                'accounts': [n for n, _d in done]}
    dest = install_skill(body.get('dir', ''), _skill_dest(body))
    return {'ok': bool(dest), 'dir': dest}


def api_skill_remove(q, body):
    """Delete a skill folder. A PERSONAL one is removed from every account that
    has it, unless the caller says otherwise — the mirror of the install
    fan-out, so "installed everywhere" cannot decay into orphans."""
    from .skills import delete_skill, delete_personal, personal_accounts
    d = body.get('dir', '')
    # this reaches shutil.rmtree, and it used to reach it with any path at all
    if not _managed_path_ok(d) or not os.path.isfile(os.path.join(d, 'SKILL.md')):
        raise BadRequest('not a skill directory claudectl manages')
    if body.get('scope') == 'personal' and body.get('all_accounts', True):
        gone = delete_personal(d)
        return {'ok': bool(gone), 'accounts': [n for n, _p in gone]}
    return {'ok': delete_skill(d), 'accounts': personal_accounts(d)}


def api_skill_create(q, body):
    from .skills import write_skill, _slug
    skill_dir = os.path.join(_skill_dest(body), _slug(body['name']))
    meta = {'name': _slug(body['name']), 'description': body.get('description', '')}
    if body.get('tools'):
        meta['allowed-tools'] = body['tools']
    default_body = (f"# {body['name']}\n\n{body.get('description', '')}\n\n"
                    f"## Instructions\n\n1. \n")
    ok = write_skill(skill_dir, meta, body.get('body') or default_body)
    return {'ok': ok, 'dir': skill_dir}


def api_global_claude_md(q, body):
    """The account-global CLAUDE.md Claude reads in every session."""
    from .mcp import GLOBAL_MD_STUB
    path = _c.global_claude_md_for(q.get('cfgdir'))
    exists = os.path.isfile(path)
    text = open(path, encoding='utf-8', errors='ignore').read() if exists else GLOBAL_MD_STUB
    return {'path': path, 'text': text, 'exists': exists,
            'accounts': [{'name': n, 'dir': d} for n, d in _c.all_config_dirs()]}


def api_global_claude_md_save(q, body):
    """Atomic, because Claude Code reads this file every session."""
    path = _c.global_claude_md_for(body.get('cfgdir'))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok = _c.write_atomic(path, body.get('text', ''))
    return {'ok': ok, 'path': path}


def api_mcp_get(q, body):
    """The MCP page itself, so `?refresh=1` gets a live probe — the 30s cache
    exists for /api/dashboard's 10-second poll, not to make this page stale."""
    from .mcp import get_mcp_status
    refresh = str((q or {}).get('refresh', '')) in ('1', 'true', 'yes')
    return {'servers': [{'name': n, 'status': s}
                        for n, s in get_mcp_status(q.get('cfgdir'), refresh=refresh)]}


def api_skills_library(q, body):
    """Copy a template, project or plugin skill into the PERSONAL scope of every
    account — `<cfgdir>/skills`, which Claude Code loads in every project."""
    from .skills import install_personal
    done = install_personal(body['dir'])
    return {'ok': bool(done), 'dir': done[0][1] if done else '',
            'accounts': [n for n, _d in done],
            'error': '' if done else 'not a skill folder'}


def api_mcp_detail(q, body):
    """`claude mcp get <name>` — the detail the TUI shows and the GUI did not."""
    from .mcp import mcp_cli
    ok, out = mcp_cli(['get', q['name']], q.get('cfgdir'), timeout=30)
    return {'ok': ok, 'name': q['name'], 'text': out}


def api_mcp_remove(q, body):
    from .mcp import mcp_cli
    ok, out = mcp_cli(['remove', body['name'], '-s', body.get('scope', 'local')],
                      body.get('cfgdir'), timeout=30)
    return {'ok': ok, 'error': '' if ok else out}


def api_mcp_add(q, body):
    """Mirrors mcp._mcp_add_with_extras: -e env vars for stdio, -H headers for
    http/sse. The GUI dropped both, so a server needing a token could be added
    from the TUI and not from here."""
    from .mcp import mcp_cli
    args = ['add', body['name']]
    if body.get('transport') in ('sse', 'http'):
        args += ['--transport', body['transport'], body['url']]
        for h in _lines(body.get('headers')):
            args += ['-H', h]
    else:
        for kv in _lines(body.get('env')):
            args += ['-e', kv]
        args += ['--', *str(body.get('command', '')).split()]
    if body.get('scope'):
        args[2:2] = ['-s', body['scope']]
    ok, out = mcp_cli(args, body.get('cfgdir'))
    return {'ok': ok, 'error': '' if ok else out}


def _lines(v):
    """A textarea or a list of strings -> the non-blank entries."""
    if isinstance(v, str):
        v = v.splitlines()
    return [x.strip() for x in (v or []) if str(x).strip()]


def api_accounts_get(q, body):
    from .accounts import _accounts, _resolved
    from .config import load_settings
    return {'accounts': [{'name': n, 'dir': d, 'resolved': _resolved(d),
                          'active': a}
                         for n, d, a in _accounts(load_settings())]}


def api_accounts_post(q, body):
    from .config import load_settings, save_settings
    from .accounts import _resolved
    s = load_settings()
    act, name = body.get('action'), body.get('name', '')
    if act == 'add':
        d = body.get('dir') or os.path.join(_c._USERPROFILE, f'.claude-{name}')
        os.makedirs(_resolved(d), exist_ok=True)
        s['accounts'] = [a for a in s.get('accounts', []) if a.get('name') != name]
        s['accounts'].append({'name': name, 'dir': d})
    elif act == 'switch':
        d = '' if name == 'default' else next(
            (a['dir'] for a in s.get('accounts', []) if a.get('name') == name), '')
        s['claude_config_dir'] = d
    elif act == 'rename':
        new = body.get('new', '')
        if not new or new == 'default' or any(
                a.get('name') == new for a in s.get('accounts', [])):
            return {'ok': False, 'error': 'name unavailable'}
        for a in s.get('accounts', []):
            if a.get('name') == name:
                a['name'] = new
    elif act == 'remove':
        d = next((a['dir'] for a in s.get('accounts', []) if a.get('name') == name), '')
        s['accounts'] = [a for a in s.get('accounts', []) if a.get('name') != name]
        if os.path.expanduser(s.get('claude_config_dir', '')) == os.path.expanduser(d):
            s['claude_config_dir'] = ''
    else:
        return {'ok': False, 'error': 'unknown action'}
    save_settings(s)
    return {'ok': True}


def api_accounts_sync(q, body):
    """The per-account provisioning diff — read-only, nothing is written."""
    from . import provision
    d = provision.diff()
    return {'clean': d['clean'], 'accounts': d['accounts'],
            'report': provision.report(d)}


def api_accounts_terminal(q, body):
    """login / parallel — spawn a terminal for the account (argv-list form)."""
    import subprocess
    from .accounts import _env_for
    from .config import get_claude_exe
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    name = body.get('name', 'claude')
    p, err = _proc.spawn_terminal([exe], env=_env_for(body.get('dir', '')),
                                  title=f'claude [{name}]')
    return {'ok': bool(p), 'error': err} if err else {'ok': True}


# ── memory suite ─────────────────────────────────────────────

def api_memory_state(q, body):
    """Everything the graph knows about itself: size, reach, spend, queue.

    The graph has always carried relations, module links, eviction names, the
    reinforcement counters and the outcome of the last automatic cycle. None of
    it left this handler, so the Memory tab could only ever show entity counts —
    it could not say what memory costs, what it dropped, or what it is doing."""
    from .memhub import _state, last_written
    from .lessons import pending_sids
    from . import hooks, memory, recall as _recall
    folder = _folder(q.get('cfgdir'), q['enc'])
    st = _state(q['path'], folder)
    mem = st['mem']
    try:
        n_unscanned = len(pending_sids(folder, mem))
    except Exception:
        n_unscanned = 0
    # most-reinforced facts: `hits` drives both recall ranking and the eviction
    # score, so which entities are actually earning their place is the one thing
    # that explains why memory looks the way it does.
    top = sorted((e for e in st['entities'] if e.get('hits')),
                 key=lambda e: -int(e.get('hits') or 0))[:8]
    tpl = hooks.TEMPLATES.get('memory-stale-on-change')
    try:
        dirty_hook = bool(tpl) and _template_installed(
            hooks, hooks._load(q.get('cfgdir')), tpl)
    except Exception:
        dirty_hook = False
    return {'generated_at': mem.get('generated_at', ''),
            'n_entities': len(st['entities']),
            'n_lessons': len(st['lessons']),
            'n_pending': len(st['pending']),
            'n_unscanned': n_unscanned,
            'n_relations': len(mem.get('relations') or []),
            'n_module_edges': len(mem.get('module_edges') or []),
            'n_modules': len(mem.get('summaries') or {}),
            'session_counter': int(mem.get('session_counter') or 0),
            'hook_on': st['hook_on'], 'rules_on': st['rules_on'],
            'auto_on': st['auto_on'],
            # what the last cycle did and what it cost. `pending_units` and the
            # cost were both recorded and shown nowhere.
            'pending_units': int(mem.get('pending_units') or 0),
            'last_extracted': int(mem.get('last_extracted') or 0),
            'last_cost_usd': mem.get('last_cost_usd') or 0,
            'cost_usd_total': mem.get('cost_usd_total') or 0,
            'cost_history': list(mem.get('cost_history') or []),
            'auto_updated': mem.get('auto_updated', ''),
            'auto_last': mem.get('auto_last') or {},
            # what eviction dropped. Stored specifically so it could be checked.
            'evicted': int(mem.get('evicted_entities') or 0),
            'evicted_names': list(mem.get('evicted_names') or []),
            'top': [{'name': e.get('name', ''), 'hits': int(e.get('hits') or 0),
                     'module': e.get('module', '')} for e in top],
            'last_failed': int(mem.get('last_failed') or 0),
            'last_skipped': int(mem.get('last_skipped') or 0),
            'last_error': mem.get('last_error') or '',
            'dirty': memory.dirty_count(q['path']),
            'dirty_hook': dirty_hook,
            'hits_pending': _recall.hits_pending(q['path'], folder),
            'budget': (st['settings'] or {}).get('memory_budget', 600),
            # what a capped cycle actually means for the user: the rest waits
            # this long. Without it "still queued" has no answer to "until when".
            'auto_interval': int((st['settings'] or {}).get('auto_memory_interval', 3600)),
            # "is this stale?" per artifact — epoch seconds, so the wire carries
            # no formatting decision. Only the artifacts that are exactly one
            # file are in here; see memhub.last_written for why the CLAUDE.md
            # blocks deliberately are not.
            'written': last_written(q['path'], folder),
            'est': st['est']}


def api_memory_entity(q, body):
    """One fact in the graph, in full: what it means and what cites it.

    The "most reinforced" list was names and a hit count — which says a fact
    matters without ever saying what the fact IS. Everything here is already in
    graph.json; nothing was reachable from the browser."""
    from .memory import load_memory
    mem = load_memory(q['path'], _folder(q.get('cfgdir'), q['enc']))
    name = q.get('name', '')
    e = next((x for x in mem.get('entities', []) if x.get('name') == name), None)
    if not e:
        return {'found': False, 'name': name}
    rels = []
    for r in mem.get('relations', []):
        if r.get('source') == name:
            rels.append({'rel': r.get('rel', 'relates'), 'other': r.get('target', ''),
                         'dir': 'out'})
        elif r.get('target') == name:
            rels.append({'rel': r.get('rel', 'relates'), 'other': r.get('source', ''),
                         'dir': 'in'})
    unit = f"{e.get('repo', '')}/{e.get('module', '')}"
    return {'found': True, 'name': name, 'type': e.get('type', ''),
            'summary': e.get('summary', ''), 'module': e.get('module', ''),
            'repo': e.get('repo', ''), 'unit': unit,
            'hits': int(e.get('hits') or 0), 'rank': int(e.get('rank') or 0),
            'status': e.get('status', ''), 'valid': bool(e.get('valid', True)),
            'kind': e.get('kind', ''),
            'created_at': e.get('created_at', ''),
            'source_files': list(e.get('source_files') or []),
            'unit_summary': (mem.get('summaries') or {}).get(unit, ''),
            'relations': rels[:24],
            # lessons carry the sessions they came from; a code entity has no
            # session link at all — recall's sidecar records names, not sids —
            # so say which it is rather than inventing a provenance.
            'sessions': list(e.get('sids') or ([e['sid']] if e.get('sid') else []))}


def api_memory_progress(q, body):
    """Live progress, and — once the lock clears — HOW the last run ended.

    The poller used to read the lock disappearing as success, which a crashed
    cycle does in its `finally` exactly like a successful one."""
    from .memory import scan_lock_status
    return {'progress': scan_lock_status(q['path']),
            'last': last_refresh(q['path'])}


def api_memory_autoscan(q, body):
    """Called each time a project is opened. Kick off an in-process memory
    refresh ONLY when the project's source has actually changed (cheap
    hash-only `is_stale` check) — so revisiting an up-to-date project neither
    re-scans nor flashes the badge. Returns whether a refresh is now running."""
    from . import memory
    path = body.get('path', '')
    folder = _folder(body.get('cfgdir'), body.get('enc', ''))
    if not path or not folder:
        return {'running': False, 'stale': False}
    running = memory.scan_lock_status(path) is not None
    if running:
        return {'running': True, 'stale': True}
    try:
        force = bool(body.get('force'))
        # `memory.refresh_on_open` is the ONE answer to this question — the TUI
        # has asked it through that function all along, while this handler
        # re-derived it from the global setting alone. So the per-project flag
        # was ignored here: a project opted into background auto-memory spent a
        # cycle every time it was opened, on top of the scheduled ones, and the
        # interval the user configured did not bound anything.
        on_open = memory.refresh_on_open(path, body.get('enc', ''))
        # only refresh when something changed (or the user forced it)
        if (force or on_open) and memory.is_stale(path, folder):
            _refresh_async(path, folder, auto_cap=None if force else 6)
            running = True
    except Exception:
        _c.log.exception('gui memory autoscan failed')
    return {'running': running, 'stale': running}


def api_memory_active(q, body):
    """Project paths whose memory is being refreshed right now (scan-lock held)
    — lets the sidebar show which projects are updating, tab-independent."""
    from . import memory, gui
    active = []
    for p in gui.list_projects():
        try:
            if memory.scan_lock_status(p['path']) is not None:
                active.append(p['path'])
        except Exception:
            pass
    return {'active': active}


def api_memory_auto_get(q, body):
    """Per-project auto-memory state for the management UI."""
    from .config import load_settings
    from . import gui, memory
    pd = load_settings().get('project_defaults') or {}
    projs = []
    for p in gui.list_projects():
        auto = bool((pd.get(p['encoded']) or {}).get('auto_memory'))
        running = False
        try:
            running = memory.scan_lock_status(p['path']) is not None
        except Exception:
            pass
        projs.append({'enc': p['encoded'], 'path': p['path'], 'name': p['name'],
                      'auto': auto, 'running': running})
    return {'projects': projs,
            'interval': load_settings().get('auto_memory_interval', 3600)}


def api_memory_auto_set(q, body):
    """Toggle a project's auto-memory opt-in (and optionally the interval)."""
    from .config import load_settings, save_settings
    s = load_settings()
    enc = body.get('enc', '')
    if enc:
        s.setdefault('project_defaults', {}).setdefault(enc, {})['auto_memory'] = \
            bool(body.get('auto'))
    if 'interval' in body:
        try:
            s['auto_memory_interval'] = max(60, int(body['interval']))
        except (TypeError, ValueError):
            pass
    save_settings(s)
    return {'ok': True}


def api_project_hide(q, body):
    """Archive a project out of the project lists, or bring it back.

    View-only: no file moves, so the sessions of a hidden project stay resumable
    and un-hiding costs one settings write. The GUI sidebar and the TUI project
    menu both read the same flag."""
    from .config import set_project_hidden
    set_project_hidden(body['enc'], bool(body.get('hidden', True)))
    return {'ok': True}


def api_lessons_get(q, body):
    from .memory import load_memory
    mem = load_memory(q['path'], _folder(q.get('cfgdir'), q['enc']))
    lessons = [e for e in mem.get('entities', []) if e.get('type') == 'lesson']
    lessons.sort(key=lambda e: (e.get('status') != 'pending',
                                -e.get('confidence', 0)))
    from .config import load_settings
    return {'lessons': [{'id': e.get('id'), 'name': e.get('name', ''),
                         'summary': e.get('summary', ''),
                         'status': e.get('status', 'pending'),
                         'kind': e.get('kind', ''),
                         'last_used': int(e.get('last_used') or 0),
                         'confidence': e.get('confidence', 0)}
                        for e in lessons],
            # decay is `counter - last_used > ttl` (lessons.apply_decay), so a
            # lesson's distance from eviction needs all three numbers. The table
            # showed confidence, which is not what decides whether it survives.
            'counter': int(mem.get('session_counter') or 0),
            'ttl': load_settings().get('memory_lessons_ttl', 30)}


def api_lessons_post(q, body):
    from .lessons import _set_status, _evict
    folder = _folder(body.get('cfgdir'), body['enc'])
    act = body.get('action')
    if act in ('approve', 'pin'):
        _set_status(body['path'], folder, body['id'],
                    'approved' if act == 'approve' else 'pinned')
    elif act == 'evict':
        _evict(body['path'], folder, body['id'])
    elif act == 'approve_all':
        from .memory import load_memory
        mem = load_memory(body['path'], folder)
        for e in mem.get('entities', []):
            if e.get('type') == 'lesson' and e.get('status') == 'pending':
                _set_status(body['path'], folder, e['id'], 'approved')
    else:
        return {'ok': False, 'error': 'unknown action'}
    return {'ok': True}


def api_ctxaudit(q, body):
    from .ctxaudit import audit_items, audit_total
    items = audit_items(q['path'], _folder(q.get('cfgdir'), q['enc']))
    return {'items': items, 'total': audit_total(items)}


def api_ctxaudit_prune_preview(q, body):
    """What a prune would remove. The GUI destroyed without asking while the
    TUI confirmed — same operation, two different contracts."""
    from .claude_md import prune_preview
    p = prune_preview(q['path'], _folder(q.get('cfgdir'), q['enc']))
    if p is None:
        return {'ok': False, 'error': 'no CLAUDE.md in this project'}
    return {'ok': True, 'old_tokens': p['old_tokens'], 'new_tokens': p['new_tokens'],
            'dropped': p['dropped'], 'changed': p['changed']}


def api_ctxaudit_prune(q, body):
    from .claude_md import prune_claude_md
    res = prune_claude_md(body['path'],
                          _folder(body.get('cfgdir'), body['enc']))
    if res is None:                # no CLAUDE.md, or the write failed
        return {'ok': False, 'error': 'no CLAUDE.md in this project'}
    old_tok, new_tok = res
    return {'ok': True, 'old_tokens': old_tok, 'new_tokens': new_tok}


#: what the History panel offers to roll back. Keys must exist in
#: diffview.target_path or a restore would have nowhere to write.
_HISTORY_KEYS = ('claude_md', 'memory_graph', 'system_prompt')


def _graph_shape(text):
    """{entities, relations, lessons} of a serialised graph, or None."""
    try:
        g = json.loads(text)
        ents = g.get('entities') or []
        return {'entities': sum(1 for e in ents if e.get('type') != 'lesson'),
                'lessons': sum(1 for e in ents if e.get('type') == 'lesson'),
                'relations': len(g.get('relations') or [])}
    except Exception:
        return None


def api_history(q, body):
    """Every replaced version claudectl still holds, newest first.

    `added`/`removed` are LINE counts, which is the right summary for a
    CLAUDE.md and pure noise for the graph: re-serialising a 300 KB JSON reports
    "+27808 -27783" whether one fact changed or all of them. For memory_graph
    the summary is the shape delta instead — entities, relations and lessons
    before against now — which is the thing you would actually restore for."""
    from . import diffview
    from .sessions import format_age
    folder = _folder(q.get('cfgdir'), q['enc'])
    keys = []
    for k in _HISTORY_KEYS:
        vs = diffview.versions(q['path'], folder, k)
        out = []
        for v in vs:
            row = dict(v, age=format_age(v['ts']))
            if k == 'memory_graph':
                row['shape'] = _graph_shape(
                    diffview.read_version(q['path'], folder, k, v['ts']))
            out.append(row)
        cur = None
        if k == 'memory_graph':
            p = diffview.target_path(q['path'], folder, k)
            if p and os.path.isfile(p):
                cur = _graph_shape(open(p, encoding='utf-8', errors='ignore').read())
        keys.append({'key': k, 'title': diffview.TITLES.get(k, k),
                     'now': cur, 'versions': out})
    return {'keys': keys}


def api_history_diff(q, body):
    from . import diffview
    folder = _folder(q.get('cfgdir'), q['enc'])
    key = q['key']
    if key not in _HISTORY_KEYS:
        raise BadRequest('unknown history key')
    old = diffview.read_version(q['path'], folder, key, q['ts'])
    p = diffview.target_path(q['path'], folder, key)
    cur = ''
    if p and os.path.isfile(p):
        try:
            cur = open(p, encoding='utf-8', errors='ignore').read()
        except Exception:
            cur = ''
    if key == 'memory_graph':
        # a line diff of two serialised graphs is thousands of lines of JSON
        # punctuation. What changed is which FACTS came and went.
        a, b = _graph_shape(cur), _graph_shape(old)
        if a is not None and b is not None:
            names = lambda t: {e.get('name', '') for e in    # noqa: E731
                               (json.loads(t).get('entities') or [])}
            try:
                back, gone = names(old) - names(cur), names(cur) - names(old)
            except Exception:
                back, gone = set(), set()
            lines = [f"--- now: {a['entities']} entities, {a['relations']} relations, "
                     f"{a['lessons']} lessons",
                     f"+++ snapshot: {b['entities']} entities, {b['relations']} relations, "
                     f"{b['lessons']} lessons", '@@ what restoring would change @@']
            lines += [f"+ {n}" for n in sorted(back)[:60]]
            lines += [f"- {n}" for n in sorted(gone)[:60]]
            if len(back) > 60 or len(gone) > 60:
                lines.append(f"@@ …and {max(0, len(back) - 60) + max(0, len(gone) - 60)} more @@")
            if not back and not gone:
                lines.append('  the same facts — only their summaries or counters moved')
            return {'title': diffview.TITLES.get(key, key), 'diff': lines}
    # old=current, new=snapshot: the diff reads as "what restoring would do"
    return {'title': diffview.TITLES.get(key, key),
            'diff': diffview.unified(cur, old, diffview.TITLES.get(key, key))}


def api_history_restore(q, body):
    from . import diffview
    if body.get('key') not in _HISTORY_KEYS:
        raise BadRequest('unknown history key')
    ok, msg = diffview.restore(body['path'],
                               _folder(body.get('cfgdir'), body['enc']),
                               body['key'], body['ts'])
    return {'ok': ok, 'message': msg} if ok else {'ok': False, 'error': msg}


def api_ctxaudit_compact(q, body):
    from .ctxaudit import append_compact_section
    return {'ok': bool(append_compact_section(body['path']))}


def api_ctxaudit_protect(q, body):
    """Fence a section of CLAUDE.md so AI compression can never rewrite it."""
    from .ctxaudit import protect_section
    md = os.path.join(body['path'], 'CLAUDE.md')
    ok = protect_section(md, body.get('text', ''))
    return {'ok': ok} if ok else {
        'ok': False, 'error': 'no matching unprotected section found'}


def api_deny_scan(q, body):
    from .denygen import scan_heavy
    return {'patterns': [{'pattern': p, 'why': w}
                         for p, w in scan_heavy(q['path'])]}


def api_deny_apply(q, body):
    from .denygen import scan_heavy, merge_deny
    pats = [p for p, _ in scan_heavy(body['path'])]
    added, existed = merge_deny(body['path'], pats)
    return {'ok': True, 'added': added, 'existed': existed}


def api_workspace_status(q, body):
    """The freshness checks as DATA, not as pre-rendered terminal lines.

    `_status_lines` is a TUI renderer: it formats emoji dots, a meter bar and
    `(+25)` weight suffixes into strings, and the GUI then ANSI-stripped them
    into a `<pre>`. Every check's name, state, weight and detail existed one
    call further down and none of it could be acted on. `compute_status` is the
    read-only structured call; `_status_lines` stays for `status_screen`."""
    from . import workspace
    m, _live, checks, score, safe = workspace.compute_status(
        q['path'], _folder(q.get('cfgdir'), q['enc']))
    return {'checks': [dict(c, weight=workspace._WEIGHTS.get(c['name'], 0))
                       for c in checks],
            'score': score, 'safe': safe,
            'generated_at': (m or {}).get('generated_at', '')}


def api_recall_preview(q, body):
    from .recall import retrieve
    from .config import load_settings
    budget = load_settings().get('memory_budget', 600)
    # log=False: a preview must not reinforce. `hits` is a term in both the
    # recall ranking and the eviction score, so logging here would let looking
    # at memory reshape it.
    r = retrieve(q['path'], _folder(q.get('cfgdir'), q['enc']),
                 q.get('q', ''), budget_tokens=budget, log=False)
    return {'context': r.get('text', ''), 'tokens': r.get('tokens', 0),
            'items': list(r.get('items') or []),
            'empty': r.get('empty', True)}


# ── CLAUDE.md, system prompt, memory map ─────────────────────

def api_claude_md_get(q, body):
    """The file, plus what it is made OF.

    CLAUDE.md is five things stacked in one file — your prose, KEEP-fenced
    regions, and three machine blocks claudectl rewrites — and the GUI showed it
    as one undifferentiated blob, so which part cost what, and which button
    regenerated which part, was unknowable. `ctxaudit` already splits it for the
    token audit; reuse that splitter rather than parsing sentinels again."""
    from . import ctxaudit
    from .memory import tokens_estimate
    p = os.path.join(q['path'], 'CLAUDE.md')
    try:
        text = open(p, encoding='utf-8', errors='ignore').read()
    except Exception:
        text = ''
    b = ctxaudit.split_blocks(text)
    n_keep = ctxaudit.keep_regions(text)
    sess = b['sessions']
    blocks = [
        {'key': 'manual', 'label': 'Your prose', 'present': bool(b['manual'].strip()),
         'tokens': tokens_estimate(b['manual'])},
        {'key': 'keep', 'label': f'Protected ({n_keep} fenced)', 'present': bool(n_keep),
         'tokens': tokens_estimate(''.join(ctxaudit._KEEP_RE.findall(text)))},
        {'key': 'autogen', 'label': 'AUTOGEN — repos and commits',
         'present': bool(b['autogen']), 'tokens': tokens_estimate(b['autogen']),
         'text': b['autogen']},
        {'key': 'sessions', 'label': 'SESSIONS — session topics',
         'present': bool(sess), 'tokens': tokens_estimate(sess), 'text': sess,
         'entries': sum(1 for l in sess.splitlines() if l.strip().startswith('- '))},
        {'key': 'memory', 'label': 'MEMORY — the digest claudectl builds',
         'present': bool(b['memory']), 'tokens': tokens_estimate(b['memory']),
         'text': b['memory']},
        # Both used to be invisible here and counted as "Your prose" — the one
        # row that promises claudectl never rewrites it. See split_blocks.
        {'key': 'agents', 'label': 'AGENTS — the subagents installed here',
         'present': bool(b['agents']), 'tokens': tokens_estimate(b['agents']),
         'text': b['agents']},
        {'key': 'loop', 'label': 'LOOP — what the background loop did',
         'present': bool(b['loop']), 'tokens': tokens_estimate(b['loop']),
         'text': b['loop']},
    ]
    return {'text': text, 'exists': bool(text), 'path': p, 'blocks': blocks,
            'tokens': tokens_estimate(text)}


def api_claude_md_scaffold(q, body):
    from .claude_md import scaffold_claude_md
    scaffold_claude_md(body['path'], _folder(body.get('cfgdir'), body['enc']))
    return {'ok': True}


def api_memory_map(q, body):
    from .claude_md import resolve_memory_files
    return {'files': [{'label': lbl, 'path': p, 'exists': exists,
                       'imports': [{'ref': r, 'exists': ok} for r, ok in imports]}
                      for lbl, p, exists, imports in resolve_memory_files(q['path'])]}


def api_open_editor(q, body):
    from .config import open_in_editor
    return {'ok': bool(open_in_editor(body['file']))}


def api_cc_settings_get(q, body):
    """Claude Code's own settings.json, per account, with the schema that says
    how to draw each control."""
    from . import ccsettings
    return {'schema': {k: {'kind': v[0], 'choices': v[1], 'help': v[2],
                           'group': v[3]}
                       for k, v in ccsettings.SCHEMA.items()},
            'groups': ccsettings.GROUPS,
            'group_help': ccsettings.GROUP_HELP,
            'accounts': [{'name': n, 'dir': d, 'values': ccsettings.read(d)}
                         for n, d in _c.all_config_dirs()]}


def api_automode(q, body):
    """Auto mode, per account: the mode sessions start in, the trusted-
    infrastructure entries this account has taught the classifier, and what the
    classifier has actually been blocking in this project.

    `claude auto-mode config` is NOT called here. It spawns the CLI, and this
    endpoint is fetched on page load; the effective-config read is its own
    endpoint so it is paid only when asked for.
    """
    from . import automode
    path = q.get('path') or ''
    return {
        'accounts': [{'name': n, 'dir': d,
                      'mode': automode.default_mode(d),
                      'environment': automode.environment(d)}
                     for n, d in _c.all_config_dirs()],
        'modes': list(_c.PERMS), 'mode_labels': list(_c.PERM_LABELS),
        'profiles': dict(_c.PERM_PROFILES),
        'denials': automode.summarise(path) if path else [],
    }


def api_automode_config(q, body):
    """The rules the classifier actually uses, straight from the CLI."""
    from . import automode
    body = body or {}           # GET carries no body — the floor test found this
    which = q.get('which') or body.get('which') or 'config'
    if which not in ('config', 'defaults'):
        raise BadRequest('which must be config or defaults')
    cfgdir = q.get('cfgdir') or body.get('cfgdir') or None
    ok, data = (automode.config_json(cfgdir) if which == 'config'
                else automode.defaults_json(cfgdir))
    return {'ok': ok, 'rules': data if ok else None,
            'error': '' if ok else str(data)}


def api_automode_set(q, body):
    """Set the starting mode and/or the environment entries for ONE account."""
    from . import automode
    cfgdir = body.get('cfgdir') or None
    msgs = []
    if 'mode' in body:
        ok, m = automode.set_default_mode(body['mode'], cfgdir)
        if not ok:
            raise BadRequest(m)
        msgs.append(m)
    if 'environment' in body:
        env = body['environment']
        if isinstance(env, str):
            env = env.splitlines()
        if not isinstance(env, list):
            raise BadRequest('environment must be a list of lines')
        ok, m = automode.set_environment(env, cfgdir)
        if not ok:
            raise BadRequest(m)
        msgs.append(m)
    if 'reset' in body:
        ok, m = automode.reset(cfgdir)
        if not ok:
            raise BadRequest(str(m))
        msgs.append('reset to defaults')
    return {'ok': True, 'message': ' · '.join(msgs) or 'nothing to do'}


def api_cc_settings_set(q, body):
    from . import ccsettings
    cfgdir = body.get('cfgdir') or None
    key = body['key']
    if body.get('all_accounts'):
        results = {n: ccsettings.write(key, body.get('value'), d)
                   for n, d in _c.all_config_dirs()}
        return {'ok': all(ok for ok, _m in results.values()),
                'per_account': {n: m for n, (_ok, m) in results.items()}}
    ok, msg = ccsettings.write(key, body.get('value'), cfgdir)
    if not ok:
        raise BadRequest(msg)
    return {'ok': True, 'message': msg}


def _loop_md_path(scope, path, cfgdir):
    """`loop.md` is Claude Code's repeating-instruction file: the project one at
    <project>/.claude/loop.md, the user one at <account>/loop.md."""
    if scope == 'user':
        return os.path.join(cfgdir or _c.config_dir, 'loop.md')
    from .paths import resolve_dir
    d = resolve_dir(path)
    if not d:
        raise BadRequest('not a directory: %s' % (path or '(empty)'))
    return os.path.join(d, '.claude', 'loop.md')


def api_loop_md_get(q, body):
    p = _loop_md_path(q.get('scope', 'project'), q.get('path', ''), q.get('cfgdir'))
    try:
        text = open(p, encoding='utf-8', errors='ignore').read()
    except OSError:
        text = ''
    return {'text': text, 'file': p, 'exists': os.path.isfile(p)}


def api_loop_md_set(q, body):
    p = _loop_md_path(body.get('scope', 'project'), body.get('path', ''),
                      body.get('cfgdir'))
    text = body.get('text', '')
    if not text.strip() and os.path.isfile(p):
        os.remove(p)
        return {'ok': True, 'file': p, 'removed': True}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    _c.write_atomic(p, text)
    return {'ok': True, 'file': p}


def api_loops(q, body):
    """Loops claudectl started, with live state read off the process and the
    transcript. See loops.py for why there is nothing else to read."""
    from . import loops
    return {'loops': loops.listing(_cfg(q)),
            'registry': loops.registry_path(_cfg(q)),
            'perms': [{'id': p, 'note': n} for p, n in loops.PERMS],
            'accounts': [{'name': n, 'dir': d} for n, d in _c.all_config_dirs()],
            'ttl_days': loops.DEFAULT_TTL // 86400}


def api_loop_start(q, body):
    """Start a loop — in a session, or in the OS scheduler.

    `kind='session'`: a `/loop` is session-scoped, so starting one IS starting a
    session; it is a normal claudectl launch (same account, agents, skills,
    system prompt, add-dirs) whose first typed message is the command.

    `kind='schedule'`: no session at all. claudectl registers a scheduler entry
    that runs headless `claude -p` on the interval, under the chosen account,
    and keeps running with claudectl closed.
    """
    from . import gui as _gui
    from . import loops
    b = body or {}
    path, enc = b.get('path', ''), b.get('enc', '')
    if not path or not enc:
        raise BadRequest('missing parameter: path')
    kind = 'schedule' if b.get('kind') == 'schedule' else 'session'
    perm = b.get('perm') or 'auto'
    if perm not in {p for p, _d in loops.PERMS}:
        raise BadRequest('unknown permission mode: %s' % perm)
    text = loops.loop_prompt(b.get('interval', ''), b.get('prompt', ''))

    if kind == 'schedule':
        if not (b.get('interval') or '').strip():
            # a self-paced loop is a thing Claude decides INSIDE a session; a
            # scheduler needs a number
            raise BadRequest('a background loop needs an interval')
        row = loops.record(path, enc, b.get('cfgdir') or '', b.get('interval', ''),
                           b.get('prompt', ''), 0, b.get('project_name', ''),
                           kind='schedule', perm=perm)
        ok, msg = loops.schedule(row['id'], b.get('interval', ''), b.get('cfgdir') or '')
        if not ok:
            loops.forget(row['id'], _cfg(b))
            return {'ok': False, 'error': msg}
        return {'ok': True, 'loop': row, 'text': text, 'message': msg}

    opts = dict(b.get('opts') or {})
    opts.update({'cfgdir': b.get('cfgdir') or '', 'prompt': text})
    for k in ('effort', 'model', 'perm', 'name', 'worktree', 'agent',
              'max_thinking', 'subagent_model'):
        opts.setdefault(k, '')
    ok, err, pid = _gui.launch_session(path, enc, 'new', opts, want_pid=True)
    if not ok:
        return {'ok': False, 'error': err}
    row = loops.record(path, enc, b.get('cfgdir') or '', b.get('interval', ''),
                       b.get('prompt', ''), pid, b.get('project_name', ''),
                       kind='session', perm=perm)
    return {'ok': True, 'loop': row, 'text': text}


def api_loop_stop(q, body):
    from . import loops
    b = body or {}
    if b.get('forget'):
        loops.forget(b.get('id', ''), _cfg(b))
        return {'ok': True, 'message': 'Removed from the board'}
    if b.get('renew'):
        ok, msg = loops.renew(b.get('id', ''), _cfg(b))
        return {'ok': ok, 'message': msg}
    ok, msg = loops.stop(b.get('id', ''), _cfg(b))
    return {'ok': ok, 'message': msg}


def api_system_prompt_get(q, body):
    folder = _folder(q.get('cfgdir'), q['enc'])
    p = os.path.join(folder, 'system-prompt.txt')
    try:
        text = open(p, encoding='utf-8', errors='ignore').read()
    except Exception:
        text = ''
    return {'text': text, 'file': p}


def api_system_prompt_set(q, body):
    folder = _folder(body.get('cfgdir'), body['enc'])
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, 'system-prompt.txt')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(body.get('text', ''))
    return {'ok': True}


def api_extra_paths_get(q, body):
    from .sessions import load_extra_paths
    return {'paths': load_extra_paths(_folder(q.get('cfgdir'), q['enc']))}


def api_extra_paths_set(q, body):
    from .sessions import save_extra_paths
    folder = _folder(body.get('cfgdir'), body['enc'])
    os.makedirs(folder, exist_ok=True)
    save_extra_paths(folder, [p.strip() for p in body.get('paths', []) if p.strip()])
    return {'ok': True}


def api_add_dirs_get(q, body):
    from .sessions import load_add_dirs
    return {'dirs': load_add_dirs(_folder(q.get('cfgdir'), q['enc']))}


def api_add_dirs_set(q, body):
    from .sessions import save_add_dirs
    folder = _folder(body.get('cfgdir'), body['enc'])
    os.makedirs(folder, exist_ok=True)
    save_add_dirs(folder, [d.strip() for d in body.get('dirs', []) if d.strip()])
    return {'ok': True}


# ── open a new project by path (mirror of the TUI's path_input) ──

def api_path_complete(q, body):
    """Live folder auto-completion for the open-project modal: same pure
    completion source the TUI's path_input uses. Returns child directories
    of the typed path (or drive roots for empty text) as full paths."""
    from .ui import path_completions, _join_path
    base, partial, names = path_completions(q.get('text', ''))
    dirs = [(_join_path(base, n) if not n.endswith((os.sep, '/')) else n)
            for n in names[:12]]
    return {'dirs': dirs, 'more': max(0, len(names) - 12)}


def api_open_path(q, body):
    """Resolve a typed folder into a launchable project — validate it's an
    existing directory and encode it, exactly like the TUI's __open_path__
    branch. Returns {ok, path, enc, name} for the launch modal to use with
    choice='new'."""
    from .paths import encode_component, resolve_dir
    cand = resolve_dir(body.get('path'))
    if not cand:
        return {'ok': False, 'error': 'Not a folder — enter a valid directory path'}
    return {'ok': True, 'path': cand, 'enc': encode_component(cand),
            'name': os.path.basename(cand) or cand}


# ── hand-off (context injection) & plan-execute ──────────────
#
# `/api/inject/sessions` used to live here: it listed every session of the
# project across every account so a Tools-tab card could offer them in a
# dropdown. The GUI action is a button on a session ROW now, so the row is the
# source and there is nothing left to pick — and the list it returned was the
# same set `/api/sessions` already serves. Deleted rather than left as an
# unreferenced route: `tests/test_endpoint_floor.py` makes a real request to
# every entry in these tables, so a dead one is a permanent cost.


def api_inject_launch(q, body):
    """Write the context file and launch a new session in a new console
    under the chosen account (mirror of context_inject.run minus menus).

    The SOURCE session is identified by `cfgdir` + `enc` + `sid`, and the folder
    derived here. It used to arrive as an absolute `body['folder']` that was
    joined and read — trusted only because the one caller got it from
    `/api/inject/sessions`, which is not a check. `cfgdir` goes through
    `PARAM_CHECKS` -> `_cfgdir_ok`, so it must name an account claudectl knows.
    """
    import subprocess
    from .context_inject import _write_context_file, CTX_FILE
    from .config import get_claude_exe, launch_defaults
    from .sessions import load_add_dirs, read_extra_paths
    from .paths import encode_component, resolve_dir

    path = body['path']
    if not resolve_dir(path):       # becomes a subprocess cwd below
        return {'ok': False, 'error': 'not a directory: %s' % (path or '(empty)')}
    src_folder = _folder(body.get('cfgdir'), body['enc'])
    ctx_path, title = _write_context_file(path, src_folder, body['sid'],
                                          body.get('account', 'default'))
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    target_dir = body.get('target_cfgdir') or _c.config_dir
    encoded = encode_component(path)
    target_folder = _store.project_folder(target_dir, encoded)
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = target_dir
    extra = read_extra_paths(target_folder)
    if extra:
        env['PATH'] = ';'.join(extra) + ';' + env.get('PATH', '')
    pointer = (f"Prior conversation context (from the "
               f"'{body.get('account', 'default')}' account, session '{title}') "
               f"is saved at {CTX_FILE.replace(os.sep, '/')}. Read it first for "
               f"background, then continue from where the user picks up.")
    args = [exe, '--append-system-prompt', pointer]
    model, perm = launch_defaults(encoded)
    if model:
        args += ['--model', model]
    if perm:
        args += ['--permission-mode', perm]
    sp = os.path.join(target_folder, 'system-prompt.txt')
    if os.path.isfile(sp):
        args += ['--system-prompt-file', sp]
    add_dirs = [d for d in load_add_dirs(target_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]
    title_arg = f'claude — {os.path.basename(path) or path}'
    p, err = _proc.spawn_terminal(args, cwd=path, env=env, title=title_arg)
    return {'ok': bool(p), 'error': err} if err else {'ok': True}




# ── job launchers for the AI features ────────────────────────

#: how many unique agents go into one authoring call. A model asked for two
#: hundred lines in one answer quietly drops some of them; forty comes back
#: complete. Batching also gives cancellation somewhere to land.
SHARPEN_BATCH = 40


def _sharpen_descriptions(scope, path):
    """Rewrite agent `description` fields — for one project, or everywhere.

    Everywhere means every account's user-level agents, every project's
    `.claude/agents`, and the claudectl library. Grouped by (name, description)
    so the SAME agent installed in twelve projects is one question and twelve
    writes, and one approval gate covers the lot: approving the same rewrite
    twelve times is not consent, it is attrition.
    """
    from . import agents as _ag, memory
    from .claude_md import _pager_confirm

    if scope == 'project':
        if not path:
            raise RuntimeError('No project open')
        d = _ag.project_agents_dir(path)
        rows = [{'scope': 'project', 'dir': d, 'project_path': path,
                 'account': '', 'name': n, 'desc': desc or '', 'path': p}
                for n, desc, _m, p in _ag.list_agents(d)]
        if not rows:
            raise RuntimeError('No agents installed in this project')
    else:
        rows = _ag.all_installed()
        if not rows:
            raise RuntimeError('No agents installed anywhere')

    groups = _ag.sharpen_groups(rows)
    keys = sorted(groups)
    job = getattr(_JOBCTX, 'job', None)
    new = {}
    for i in range(0, len(keys), SHARPEN_BATCH):
        if job is not None and job['cancel_event'].is_set():
            return {'ok': False, 'cancelled': True}
        batch = keys[i:i + SHARPEN_BATCH]
        out = (memory._claude_stdin(_ag.sharpen_prompt(batch),
                                    cwd=path or '.') or '').strip()
        new.update(_ag.parse_sharpened(out))
    if not new:
        raise RuntimeError('Claude returned nothing usable')

    changed = [(n, d, groups[(n, d)]) for (n, d) in keys
               if new.get(n) and new[n].strip() != d]
    if not changed:
        return {'ok': True, 'updated': [], 'locations': 0}

    preview = '\n\n'.join(
        '%s  (%d location%s)\n  was: %s\n  now: %s'
        % (n, len(where), '' if len(where) == 1 else 's', d or '(none)', new[n])
        for n, d, where in changed)
    if not _pager_confirm('AGENT DESCRIPTIONS — approve to write', preview):
        return {'ok': False, 'rejected': True}

    # one pass per directory, then the project-only files once per project —
    # write_routing_block rewrites CLAUDE.md, so doing it per agent would
    # rewrite the same file once for every agent in it
    by_dir, projects = {}, set()
    for _n, _d, where in changed:
        for r in where:
            by_dir.setdefault(r['dir'], {})[r['name']] = new[r['name']]
            if r['project_path']:
                projects.add(r['project_path'])
    updated, locations = set(), 0
    for d, mapping in by_dir.items():
        done = _ag.apply_descriptions_dir(d, mapping)
        updated.update(done)
        locations += len(done)
    for p in sorted(projects):
        try:
            _ag.write_routing_block(p)
            _ag.write_agent_index(p)
        except Exception:
            _c.log.exception('agents: routing refresh failed for %s', p)
    return {'ok': True, 'updated': sorted(updated), 'locations': locations,
            'projects': len(projects)}


def api_job_start(q, body):
    kind = body.get('kind', '')
    path = body.get('path', '')
    enc = body.get('enc', '')
    folder = _folder(body.get('cfgdir'), enc) if enc else None
    name = os.path.basename(path) or path

    if kind == 'memory_build':
        from .memory import refresh_memory
        jid = start_job('Building memory', lambda: _memfn(refresh_memory, path, folder, name))
    elif kind == 'memory_ask':
        from .memory import ask_memory
        question = body.get('question', '')
        jid = start_job('Asking memory', lambda: ask_memory(path, folder, question))
    elif kind == 'lessons_scan':
        from . import lessons, memory
        def _scan():
            pend = lessons.pending_sids(folder, memory.load_memory(path, folder))
            added, scanned = lessons.scan_sessions(path, folder, pend)
            return {'added': added, 'scanned': scanned}
        jid = start_job('Learning from sessions', _scan)
    elif kind == 'rules_sync':
        # rewriting the rule files was reachable only as a SIDE EFFECT of
        # toggling the rules checkbox off and on again — which is not a thing
        # anyone would guess, and it is free (no Claude call).
        from . import memrules, memory
        jid = start_job('Rebuilding rules', lambda: {
            'written': len(memrules.sync_rules(path, folder,
                                               memory.load_memory(path, folder)))})
    elif kind == 'ai_scaffold':
        from .claude_md import ai_scaffold_claude_md
        jid = start_job('AI-analyzing project', lambda: ai_scaffold_claude_md(path, folder))
    elif kind == 'ai_compress':
        from .claude_md import ai_compress_claude_md
        jid = start_job('Compressing CLAUDE.md', lambda: ai_compress_claude_md(path, folder))
    elif kind == 'mcp_analyze':
        from .mcp import analyze_mcp_tools, update_global_claude_md_mcp
        mcp_name = body.get('name', '')
        cfgdir = body.get('cfgdir')
        def _an():
            doc = analyze_mcp_tools(mcp_name)
            if not doc:
                raise RuntimeError('No output from Claude — MCP may need authentication')
            # the doc rides back in the result: analyze used to write it into a
            # file the GUI had no way to open, so it showed nothing at all
            return {'written': update_global_claude_md_mcp(mcp_name, doc, cfgdir),
                    'doc': doc}
        jid = start_job(f'Analyzing MCP {mcp_name}', _an)
    elif kind == 'agent_ai':
        # `agents.generate_agent_ai`, NOT `_new_agent_ai`: the latter is the TUI
        # flow and opens a `menu()`, which no job thread can answer — it blocked
        # in wait_event() until the six-hour stuck-reaper. `inputs` is gone with
        # it; the fields arrive as real body parameters now, which also fixes the
        # description having been fed in as the agent's NAME.
        from .agents import generate_agent_ai
        name = (body.get('name') or '').strip()
        if not name:
            raise BadRequest('name is required')
        scope = 'project' if body.get('scope') == 'project' else 'user'
        jid = start_job(f'Generating agent {name}',
                        lambda: generate_agent_ai(
                            name, body.get('description', ''), scope,
                            path or None, body.get('category', '')))
    elif kind == 'hook_ai':
        # cfgdir is forwarded: the hooks page has an account selector, and
        # without it every AI-generated hook landed on the active account no
        # matter which one you were looking at.
        from .hooks import _ai_hook
        cfgdir = body.get('cfgdir') or None
        jid = start_job('Generating hook', lambda: _ai_hook(cfgdir),
                        inputs=[body.get('description', '')])
    elif kind == 'work_scan':
        # One call, findings persisted into the graph. No approval gate: it
        # writes advice, not code, and gating a read-only suggestion behind a
        # diff nobody can act on is ceremony rather than safety.
        from . import brief as _brief
        folder = _folder(body.get('cfgdir'), body['enc'])
        jid = start_job('Scanning for work',
                        lambda: _brief.run_scan(path, folder))
    elif kind == 'agent_desc_ai':
        # The one field Claude Code routes on. Rewriting it is the difference
        # between an agent that is installed and an agent that gets picked —
        # see the note above agents.write_routing_block.
        #
        # `scope` rather than a second job kind: the two differ only in which
        # directories they collect, and the gate, the parse and the writer are
        # identical. Defaults to 'all' because the control now lives on the
        # global Agents page, which has no open project to scope to.
        from . import agents as _ag
        scope = body.get('scope') or ('project' if path else 'all')
        label = ('Sharpening agent descriptions' if scope == 'project'
                 else 'Sharpening every agent description')
        jid = start_job(label, lambda: _sharpen_descriptions(scope, path))
    elif kind == 'loop_ai':
        # loop.md is the prompt a bare `/loop` runs, over and over, unattended.
        # It goes through the same approval gate as every other generated file:
        # a repeating instruction nobody read is the last thing to write blind.
        from . import memory
        from .claude_md import _pager_confirm
        scope = body.get('scope', 'project')
        goal = body.get('description', '')
        md_path = _loop_md_path(scope, path, body.get('cfgdir'))

        def _loopmd():
            prompt = (
                "Write the body of a Claude Code `loop.md` file.\n\n"
                "`loop.md` is the default prompt a bare `/loop` runs on every "
                "iteration, unattended, in this repository. It is plain markdown "
                "with no frontmatter and no title — write it as if typing the "
                "prompt directly.\n\n"
                f"What the user wants the loop to do each iteration:\n{goal}\n\n"
                "Rules for what you write:\n"
                "- Give it a clear stopping condition and say what to do when "
                "there is nothing to do (one line, no work).\n"
                "- Prefer checks that are cheap to repeat; say what to skip when "
                "nothing changed.\n"
                "- Be explicit about anything irreversible: never push, delete or "
                "release unless the instruction says so.\n"
                "- Under 25 lines. No preamble, no code fences, no explanation — "
                "output the file body only.")
            content = (memory._claude_stdin(prompt, cwd=path or '.') or '').strip()
            if not content:
                raise RuntimeError(memory.why_failed())
            if not _pager_confirm(f'loop.md ({scope}) — approve to write', content):
                return {'ok': False, 'rejected': True}
            os.makedirs(os.path.dirname(md_path), exist_ok=True)
            ok = _c.write_atomic(md_path, content if content.endswith('\n')
                                 else content + '\n')
            return {'ok': ok, 'file': md_path, 'text': content}
        jid = start_job('Writing loop.md', _loopmd)
    elif kind == 'skill_ai':
        from . import skills, memory
        from .claude_md import _pager_confirm
        sk_name = body.get('name', '')
        role = body.get('description', '') or sk_name
        proj = path or None
        def _skill():
            prompt = skills.build_ai_prompt(sk_name, role, proj)
            content = (memory._claude_stdin(prompt, cwd=path or '.') or '').strip()
            if not content:
                raise RuntimeError(memory.why_failed())
            if not _pager_confirm(f'SKILL / {skills._slug(sk_name)} — approve to write',
                                  content):
                return {'ok': False, 'rejected': True}
            d = skills.write_skill_raw(_skill_dest(body), sk_name, content)
            return {'ok': bool(d), 'dir': d}
        jid = start_job(f'Generating skill {skills._slug(sk_name)}', _skill)
    elif kind == 'sync_accounts':
        from . import provision, plugins as plugins_mod
        kinds = tuple(body.get('kinds') or provision.KINDS)
        def _sync():
            d = provision.diff()
            if d['clean']:
                return {'clean': True, 'done': []}
            job = getattr(_JOBCTX, 'job', None)
            def _note(acct, k, detail, ok):
                if job is not None:
                    job['messages'].append({'ok': ok, 'text': f'{acct}: {k} {detail}'})
            # every plugin goes through the same review gate the single-account
            # install uses: four more accounts is four more exposures
            done = provision.apply(d, kinds=kinds, progress=_note,
                                   review=plugins_mod.review_plugin)
            return {'clean': False,
                    'done': [{'account': a, 'kind': k, 'detail': t, 'ok': o}
                             for a, k, t, o in done]}
        jid = start_job('Syncing accounts', _sync)
    elif kind == 'project_setup':
        # the TUI's one-key `!`: scaffold CLAUDE.md, then build memory (which
        # syncs the path-scoped rules on its way through)
        from .claude_md import scaffold_claude_md
        from .memory import refresh_memory
        def _setup():
            wrote = scaffold_claude_md(path, folder)
            mem = _memfn(refresh_memory, path, folder, name)
            return {'claude_md': bool(wrote),
                    'entities': len((mem or {}).get('entities', []))}
        jid = start_job(f'Setting up {name}', _setup)
    elif kind == 'review':
        from .review import run_review
        staged = bool(body.get('staged'))
        base = body.get('base') or None
        jid = start_job('Reviewing changes',
                        lambda: run_review(path, folder, staged=staged, base=base))
    elif kind == 'plan_make':
        from .plan_execute import _plan, write_plan_file, optimize_plan_council
        from .config import load_settings, omniroute_env
        s = load_settings()
        model = body.get('model') or s.get('plan_model', '')
        task = body.get('task', '')
        effort = body.get('effort', '')
        council = bool(body.get('council'))
        # plan under the same account chosen for execution -- otherwise the
        # plan call silently runs under whatever account claudectl itself is
        # active as, regardless of what the user picked in the GUI.
        cfgdir = body.get('account') or ''
        # council must route through the SAME channel the user picked for
        # execution (body['via']), not the account-wide default setting --
        # else a stale omniroute_exec_model default silently routes every
        # council call at an unreachable proxy, _headless swallows the
        # errors, and optimize_plan_council quietly no-ops the plan back
        # unchanged with no error shown.
        via = body.get('via', 'anthropic')
        omni_env = omniroute_env(s, model='_') if via == 'omniroute' else {}

        # Pre-flight: fail fast (~5s) if the endpoint the headless `claude`
        # call will talk to is unreachable, instead of spawning a job that
        # spins for up to plan_timeout_sec. _plan() inherits the process env;
        # the council (omni_env) may target a different base, so check both.
        from .plan_execute import check_endpoint
        try:
            check_endpoint(os.environ.get('ANTHROPIC_BASE_URL', ''))
            check_endpoint((omni_env or {}).get('ANTHROPIC_BASE_URL', ''))
        except RuntimeError as e:
            return {'ok': False, 'error': str(e)}

        def _make():
            plan = _plan(task, model, path, effort, cfgdir)
            if not plan:
                raise RuntimeError(_subprocess_error_detail()
                                   or 'Planning failed or produced no output')
            if council:
                plan = optimize_plan_council(task, plan, path, omni_env=omni_env, cfgdir=cfgdir)
            plan_path = write_plan_file(path, task, plan)
            if not plan_path:
                raise RuntimeError('Could not save plan file')
            return {'plan': plan, 'plan_path': plan_path}
        jid = start_job(f'Writing plan ({model}){" + council" if council else ""}', _make)
    elif kind == 'plan_launch':
        from .plan_execute import build_exec_launch, write_plan_file
        # waiver: compared against the account picked for execution, to decide
        # whether a cfgdir override is needed. The ACTIVE account is the right
        # baseline for that comparison.
        from .config import load_settings, omniroute_env, config_dir
        from . import omniroute, ui
        task = body.get('task', '')
        plan_text = body.get('plan_text', '')
        per_step = bool(body.get('per_step'))
        cfgdir = body.get('account') or ''
        exec_folder = folder
        if cfgdir and cfgdir != config_dir:
            from .paths import encode_component
            exec_folder = _store.project_folder(cfgdir, encode_component(path))

        def _launch():
            import subprocess
            s = load_settings()
            via = body.get('via', 'anthropic')
            omni_env = omniroute_env(s, model='_') if via == 'omniroute' else {}
            # write user-edited plan text before launching
            if plan_text:
                write_plan_file(path, task, plan_text)
            if omni_env:
                ok, msg = omniroute.ensure_running(s.get('omniroute_base_url', ''))
                ui.flash(f'OmniRoute: {msg}', ok=ok)
                if not ok:
                    raise RuntimeError(msg)
                # catch a stale/renamed omniroute_exec_model here, before
                # launching -- otherwise the exec session opens fine and only
                # fails once `claude` itself tries the model, deep inside the
                # new terminal with no easy path back to fix the setting.
                _exec_model_check = body.get('model') or s.get('omniroute_exec_model') or omniroute.AUTO_MODEL
                if _exec_model_check != omniroute.AUTO_MODEL:
                    available = [mid for mid, _lbl in omniroute.list_models(
                        s.get('omniroute_base_url', ''), s.get('omniroute_api_key', ''))]
                    if available and _exec_model_check not in available:
                        raise RuntimeError(
                            f"OmniRoute: exec model '{_exec_model_check}' is no longer available — "
                            "pick a new one in Settings or switch to Auto")
                # omniroute_env() already repointed ANTHROPIC_BASE_URL at the
                # failover proxy when candidates are configured, so it has to be
                # up before claude is handed that URL.
                from . import failover
                if failover.enabled(s):
                    _fok, _fmsg = failover.ensure_running(s)
                    ui.flash(f'Failover proxy: {_fmsg}', ok=_fok)
                    if not _fok:
                        raise RuntimeError(_fmsg)
                # context-window warning for free-tier OmniRoute models
                try:
                    _ctx = 0
                    _md = os.path.join(path, 'CLAUDE.md') if path else ''
                    if _md and os.path.isfile(_md):
                        _ctx += os.path.getsize(_md)
                    _rd = os.path.join(path, '.claude', 'rules') if path else ''
                    if _rd and os.path.isdir(_rd):
                        for _f in os.listdir(_rd):
                            _fp = os.path.join(_rd, _f)
                            if os.path.isfile(_fp) and _f.endswith('.md'):
                                _ctx += os.path.getsize(_fp)
                    _ctx += len(plan_text or '') * 3
                    if _ctx // 4 > 8000:
                        ui.flash(f"OmniRoute: CLAUDE.md + rules + plan ≈ {_ctx // 4 // 1000}k tokens — "
                                 "small-context model may degrade", ok=False, secs=3)
                except Exception:
                    pass
            if body.get('model'):
                model = body['model']
            elif omni_env:
                model = s.get('omniroute_exec_model') or omniroute.AUTO_MODEL
            else:
                model = s.get('exec_model', '')
            from .paths import resolve_dir
            if not resolve_dir(path):   # becomes a subprocess cwd below
                raise RuntimeError('not a directory: %s' % (path or '(empty)'))
            args, env = build_exec_launch(path, exec_folder, task, model, omni_env, cfgdir)
            if not args:
                raise RuntimeError('claude.exe not found')
            title = f"claude — {os.path.basename(path)}"
            _p, err = _proc.spawn_terminal(args, cwd=path, env=env, title=title)
            if err:
                raise RuntimeError(err)
            return {'model': model, 'via': via}
        jid = start_job('Launching execute session' + (' (per-step)' if per_step else ''), _launch)
    elif kind == 'plan_replan':
        from .plan_execute import replan_from_plan
        task = body.get('task', '')
        plan_text = body.get('plan_text', '')
        feedback = body.get('feedback', '')
        model = body.get('model') or 'claude-sonnet-5'
        effort = body.get('effort', '')
        cfgdir = body.get('account') or ''

        # Pre-flight: same fast-fail guard as plan_make — replan() also spawns
        # headless `claude` under the process env.
        from .plan_execute import check_endpoint
        try:
            check_endpoint(os.environ.get('ANTHROPIC_BASE_URL', ''))
        except RuntimeError as e:
            return {'ok': False, 'error': str(e)}

        def _replan():
            revised = replan_from_plan(plan_text or task, feedback, model, path, effort, cfgdir)
            if not revised:
                raise RuntimeError(_subprocess_error_detail()
                                   or 'Re-plan failed or produced no output')
            return {'plan': revised}
        jid = start_job('Re-planning with feedback', _replan)
    elif kind == 'claude_update':
        from . import versions
        target = str(body.get('target', '') or '')
        def _cu():
            ok, msg = versions.update_claude(target)
            if not ok:
                raise RuntimeError(msg or 'update failed')
            return {'message': msg, 'installed': versions.installed_version()}
        jid = start_job('Updating Claude Code' + (f' to {target}' if target else ''), _cu)
    elif kind == 'claudectl_update':
        from . import versions
        def _su():
            ok, msg = versions.update_self()
            if not ok:
                raise RuntimeError(msg or 'update failed')
            return {'message': msg}
        jid = start_job('Updating claudectl', _su)
    elif kind == 'plugin_update':
        from . import versions
        key = str(body.get('key', '') or '')
        def _pu():
            ok, msg = versions.update_plugin(key)
            if not ok:
                raise RuntimeError(msg or 'update failed')
            return {'message': msg}
        jid = start_job(f'Updating {key}', _pu)
    elif kind == 'marketplace_refresh':
        from . import versions
        mkt = str(body.get('name', '') or '')
        def _mr():
            ok, msg = versions.update_marketplaces(mkt)
            if not ok:
                raise RuntimeError(msg or 'refresh failed')
            return {'message': msg}
        jid = start_job('Refreshing marketplaces', _mr)
    elif kind == 'skill_git_install':
        from . import skills
        from .config import load_settings
        url = body.get('url', '')
        # the same scope choice every other install makes: personal unless the
        # caller asked for this project
        proj = path if body.get('scope') == 'project' else None
        cfgdir = body.get('cfgdir')

        def _install():
            exec_model = load_settings().get('omniroute_exec_model', '')
            ok, msg = skills.install_from_git(url, proj, exec_model, cfgdir)
            if not ok:
                raise RuntimeError(msg)
            return {'message': msg}
        jid = start_job(f'Installing from {url}', _install)
    elif kind == 'omniroute_ensure':
        from . import omniroute
        from .config import load_settings

        def _ensure():
            s = load_settings()
            ok, msg = omniroute.ensure_running(s.get('omniroute_base_url', ''))
            return {'ok': ok, 'message': msg}
        jid = start_job('Starting OmniRoute', _ensure)
    elif kind == 'omniroute_probe':
        from . import omniroute
        from .config import load_settings

        def _probe():
            s = load_settings()
            base, key = s.get('omniroute_base_url', ''), s.get('omniroute_api_key', '')
            ids = body.get('models') or []
            if not ids:
                usable, autos, _ex = omniroute.usable_models(base, key)
                # auto/* last: the meta-routers add a server-side selection step
                # that itself hangs, so they are the slowest thing to probe and
                # the least reliable thing to route through.
                ids = [u['id'] for u in usable] + autos[:1]
            # Bounded by default — an unbounded verify run costs minutes. `full`
            # forces a re-probe of models cached as permanently dead.
            # Small by default: each probe is a real billed request, and on free
            # tiers repeated runs exhaust the key (confirmed — probing pushed
            # OpenRouter's free models into 'Key limit exceeded'). Enough to seed
            # a failover list; the proxy refines it from real turns for free.
            full = bool(body.get('full'))
            res = omniroute.probe_models(
                base, ids, key,
                timeout=int(body.get('timeout') or 15),
                want=int(body.get('want') or 3),
                budget=int(body.get('budget') or 45),
                skip={} if full else None)
            omniroute.save_dead(res, clear=full)
            working = [r['id'] for r in res if r['ok']]
            return {'ok': bool(working), 'results': res, 'working': working}
        jid = start_job('Verifying models', _probe)
    elif kind == 'failover_stop':
        from . import failover

        def _fstop():
            ok, msg = failover.stop_running()
            return {'ok': ok, 'message': msg}
        jid = start_job('Stopping failover proxy', _fstop)
    elif kind == 'omniroute_test_connection':
        from . import omniroute
        conn_id = body.get('conn_id', '')

        def _test():
            ok, msg = omniroute.cli_test_connection(conn_id)
            return {'ok': ok, 'message': msg}
        jid = start_job(f'Testing {conn_id}', _test)
    elif kind == 'omniroute_live_test':
        from . import omniroute
        from .config import load_settings
        model = body.get('model') or omniroute.AUTO_MODEL

        def _live():
            s = load_settings()
            ok, used, msg = omniroute.test_live(
                s.get('omniroute_base_url', ''), model, s.get('omniroute_api_key', ''))
            return {'ok': ok, 'model_used': used, 'message': msg}
        jid = start_job(f'Sending a real test request via {model}', _live)
    else:
        # 400, not a 200 carrying ok:false — the SPA treats a 200 as "the
        # server understood me" and shows the generic failure toast
        raise BadRequest('unknown job kind %r' % (kind,))
    return {'ok': True, 'job': jid}


def _memfn(refresh_memory, path, folder, name):
    from .memory import acquire_scan_lock, clear_scan_lock
    # refresh_memory reports per-module progress via the scan-lock file, but
    # only does anything if THIS process holds the lock — a bg-scan worker
    # acquires it for itself; a foreground GUI job must too, or the GUI's
    # progress poll (/api/memory/progress) always reads back None.
    got = acquire_scan_lock(path)
    try:
        mem = refresh_memory(path, folder, name)
    finally:
        if got:
            clear_scan_lock(path)
    return {'entities': len(mem.get('entities', [])),
            'pending_units': mem.get('pending_units', 0)}


# ── Plan → Execute — resume a previously-approved plan ────────
# Lets the Plan → Execute tab skip straight to the approve/execute editor
# with a plan that's already on disk (or hand-pasted), instead of forcing a
# regeneration through the plan model every time a launch attempt fails or
# the browser gets reloaded.

def api_plan_last(q, body):
    """Read back <project>/.claudectl/plan-latest.md, split into the task
    title write_plan_file() stamps on it and the plan body. {'exists': False}
    if there's no saved plan for this project yet."""
    import re
    from .plan_execute import PLAN_FILE
    path = q.get('path', '')
    if not path:
        return {'exists': False}
    plan_path = os.path.join(path, PLAN_FILE)
    try:
        with open(plan_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return {'exists': False}
    task = ''
    m = re.match(r'^# Plan: (.*)\n\n', text)
    if m:
        task = m.group(1)
        text = text[m.end():]
    return {'exists': True, 'task': task, 'plan': text.strip()}


# ── OmniRoute — free-tier execution backend ───────────────────
# github.com/diegosouzapw/OmniRoute (MIT, diegosouzapw). Self-hosted local
# proxy speaking the Anthropic Messages API natively — never returns the raw
# api_key to the frontend, status/models only.

def api_omniroute_status(q, body):
    from . import omniroute
    from .config import load_settings
    s = load_settings()
    base, key = s.get('omniroute_base_url', ''), s.get('omniroute_api_key', '')
    # ONE concurrent fetch of both payloads, then everything is derived locally.
    # Each OmniRoute round trip costs ~2s on a loaded instance, so the previous
    # five serial calls made this handler a ~13s page stall.
    entries, h = omniroute.fetch_both(base, key)
    summary = h.get('summary') or {}
    usable, _autos, _ex = omniroute.classify_models(entries, h)
    return {
        'reachable': bool(entries or h.get('providers')),
        'model_count': len(entries),
        'configured': summary.get('configuredCount', 0),
        'active': summary.get('activeCount', 0),
        # providerHealth over HTTP is the signal that works here; the CLI is
        # secondary because `providers list --json` crashes on this platform and
        # returns [] even with providers connected (see module doc).
        'providers': h.get('providers', []),
        'lockouts': h.get('lockouts', []),
        'connections': omniroute.cli_connections(),
        'usable_count': len(usable),
    }


def api_omniroute_models(q, body):
    """Models that can actually serve a session, not the whole routable catalog.

    Each entry's own ``owned_by`` is cross-referenced against live providerHealth
    — the previous version guessed the provider from a hardcoded id-prefix table,
    which mislabelled every provider not in the table and then failed open, so
    models from providers that were never configured were offered as choices.
    That is how a launch lands on 'minimax-m3-free' (owned_by 'opencode', not
    connected) and 401s on every turn.

    ``all=1`` returns the unfiltered catalog for the user who wants to see it.
    """
    from . import omniroute
    from .config import load_settings
    s = load_settings()
    base = s.get('omniroute_base_url', '')
    key = s.get('omniroute_api_key', '')

    entries, h = omniroute.fetch_both(base, key)

    if str(q.get('all') or '') in ('1', 'true'):
        ids = ['auto/coding'] + [m['id'] for m in entries if m['id'] != 'auto/coding']
        return {'models': ids,
                'labels': {m['id']: (m.get('name') or m['id']) for m in entries},
                'usable': [], 'excluded': {}, 'filtered': False}

    usable, autos, excluded = omniroute.classify_models(entries, h)
    if not usable and not autos:
        return {'models': ['auto/coding'],
                'labels': {'auto/coding': 'auto/coding (dynamic router)'},
                'usable': [], 'excluded': excluded, 'filtered': True}

    ids, labels = [], {}
    for a in (['auto/coding'] + [x for x in autos if x != 'auto/coding']):
        ids.append(a)
        labels[a] = a + (' (dynamic router)' if a == 'auto/coding' else '')
    for u in usable:
        ids.append(u['id'])
        ctx = ('%dk' % (u['context'] // 1000)) if u['context'] else ''
        tail = ' · '.join(x for x in (u['provider'], ctx,
                                      'recovering' if u['state'] == 'HALF_OPEN' else '') if x)
        labels[u['id']] = '%s — %s' % (u['label'], tail) if tail else u['label']
    return {'models': ids, 'labels': labels, 'usable': usable,
            'excluded': excluded, 'filtered': True}


def api_plan_edit(q, body):
    from .plan_execute import edit_plan
    plan = body.get('plan', '')
    action = body.get('action', '')
    index = body.get('index')
    text = body.get('text', '')
    if index is not None:
        try:
            index = int(index)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'invalid index'}
    try:
        result = edit_plan(plan, action, index=index, text=text)
        return {'ok': True, 'plan': result}
    except ValueError as e:
        return {'ok': False, 'error': str(e)}


# ── dispatch table (method, path) → handler(q, body) ─────────

# ── plugins & worktrees ──────────────────────────────────────

def api_plugins(q, body):
    """Marketplaces, installed plugins, and what each one ships — for ONE
    account, plus which OTHER accounts have each of them.

    The per-account half is the point: a plugin the user installed is a property
    of THEM, and this page reported only whichever account was active, so four
    of five accounts having nothing was invisible from here.
    """
    from . import plugins
    cfgdir = q.get('cfgdir')
    out = plugins.summary(cfgdir)
    per = [(n, d, plugins.summary(d)) for n, d in _c.all_config_dirs()]
    out['accounts'] = [{'name': n, 'dir': d,
                        'plugins': len(a['plugins']),
                        'marketplaces': len(a['marketplaces'])}
                       for n, d, a in per]
    have_p, have_m = {}, {}
    for n, _d, a in per:
        for pl in a['plugins']:
            have_p.setdefault(pl['key'], []).append(n)
        for m in a['marketplaces']:
            have_m.setdefault(m['name'], []).append(n)
    for pl in out['plugins']:
        pl['on_accounts'] = have_p.get(pl['key'], [])
    for m in out['marketplaces']:
        m['on_accounts'] = have_m.get(m['name'], [])
    return out


def api_versions(q, body):
    """claudectl and the installed Claude Code against what has been released,
    every plugin against what its marketplace offers, and the model catalogue
    against what Anthropic currently serves.

    All four ride one route rather than getting one each: it is the same
    question — is what you have still what exists? — and a new endpoint per
    subject would be a new row in docs/api.md and the parity gate for no new
    capability. `?refresh=1` is also the only place a request can force the
    model catalogue to re-fetch; everything else reads its daily cache.
    """
    from . import versions
    from . import models as _mods
    refresh = str((q or {}).get('refresh', '')) in ('1', 'true', 'yes')
    if refresh:
        _mods.fetch(refresh=True)      # the only place a render can force one
    mst = _mods.status()
    mst['notices'] = _mods.notices()
    return {'claude': versions.status(refresh=refresh),
            'claudectl': versions.self_status(refresh=refresh),
            'models': mst,
            'plugins': versions.plugin_rows()}


def api_provenance(q, body):
    """{kind: {name: plugin_key}} — which rows in the skill/agent/hook managers
    came from a bundle rather than from the user. The flat lists those managers
    show are otherwise unreadable after a couple of marketplace installs, and
    the obvious action on an unrecognised entry — delete it — can break a
    plugin."""
    from . import plugins
    return {'provenance': plugins.provenance_index()}


def _plugin_targets(body):
    """Which accounts a plugin/marketplace mutation acts on.

    ADDING defaults to every account — what you install is a property of you.
    REMOVING defaults to the one account named, because deleting from four
    accounts you did not name is not a fan-out, it is a surprise.
    """
    b = body or {}
    if b.get('cfgdir'):
        return [(b.get('account') or b['cfgdir'], b['cfgdir'])]
    return _c.all_config_dirs() if b.get('scope') == 'all' else [('active', None)]


def api_plugin_marketplace_add(q, body):
    from . import plugins
    b = body or {}
    targets = _c.all_config_dirs() if b.get('scope', 'all') == 'all' \
        else _plugin_targets(b)
    done, errs = [], []
    for name, d in targets:
        ok, msg = plugins.add_marketplace(b.get('source', ''), cfgdir=d)
        (done if ok else errs).append('%s: %s' % (name, msg) if not ok else name)
    return {'ok': bool(done), 'accounts': done,
            'message': ('Added to ' + ', '.join(done) if done else '')
                       + ('  —  failed ' + '; '.join(errs) if errs else '')}


def api_plugin_marketplace_remove(q, body):
    from . import plugins
    b = body or {}
    ok, msg = plugins.remove_marketplace(b.get('name', ''), cfgdir=b.get('cfgdir'))
    return {'ok': ok, 'message': msg}


def api_plugin_install(q, body):
    """Install into every account by default, each behind the review gate."""
    from . import plugins
    b = body or {}
    name, mkt = b.get('name', ''), b.get('marketplace', '')
    done, errs = [], []
    for acct, d in _plugin_targets(dict(b, scope=b.get('scope', 'all'))):
        ok, msg = plugins.install_plugin(name, mkt, cfgdir=d)
        (done if ok else errs).append(acct if ok else '%s: %s' % (acct, msg))
    return {'ok': bool(done), 'accounts': done,
            'message': ('Installed into ' + ', '.join(done) if done else '')
                       + ('  —  failed ' + '; '.join(errs) if errs else '')}


def api_plugin_remove(q, body):
    from . import plugins
    b = body or {}
    ok, msg = plugins.remove_plugin(b.get('key', ''), cfgdir=b.get('cfgdir'))
    return {'ok': ok, 'message': msg}


def api_worktrees(q, body):
    """The board: every repo under this project, its worktrees, and the session
    working in each. A project is often a PARENT of repos rather than a repo."""
    from . import worktrees
    path = q.get('path', '')
    if not path:
        return {'repo': False, 'repos': []}
    enc = q.get('enc', '')
    return worktrees.project_board(path, _folder(q.get('cfgdir'), enc) if enc else None)


def api_worktree_merge(q, body):
    """Merge a worktree branch, behind the standard approval gate.

    The diff is shown through diffview.confirm — the same path every other
    destructive write in claudectl takes — so a merge is never something this
    endpoint decides on its own.
    """
    from . import worktrees, diffview
    path = (body or {}).get('path', '')
    branch = (body or {}).get('branch', '')
    if not path or not branch:
        return {'ok': False, 'message': 'path and branch required'}
    preview = worktrees.diff(path) or f'(no uncommitted diff in {path})'
    head = (f'Merge {branch} into the checked-out branch of '
            f'{os.path.basename(path)}?\n\n')
    if not diffview.confirm('', head + preview[:20000], f'Merge {branch}'):
        return {'ok': False, 'message': 'Cancelled'}
    ok, msg = worktrees.merge_into_main(path, branch)
    return {'ok': ok, 'message': msg}


def api_worktree_diff(q, body):
    from . import worktrees
    return {'diff': worktrees.diff(q.get('wt', ''))}


def _cfg(d):
    """The account config dir a request names, or the active one."""
    return (d or {}).get('cfgdir') or _c.config_dir


def api_output_styles(q, body):
    """Every style, WHERE the active one is pinned, and the starters to copy.

    `active_scope` is what the page could not say before: a project's
    settings.json shadows the account's, so two files can name a style and only
    one of them is in force."""
    from . import outputstyles
    path = q.get('path') or None
    return {'styles': outputstyles.listing(path, _cfg(q)),
            'active': outputstyles.current(path, _cfg(q)),
            'active_scope': outputstyles.active_scope(path, _cfg(q)),
            'starters': outputstyles.starters(),
            'user_dir': os.path.join(_c.resolve_config_dir(_cfg(q)), 'output-styles'),
            'project_dir': (os.path.join(path, '.claude', 'output-styles')
                            if path else '')}


def api_output_style_read(q, body):
    from . import outputstyles
    name = q.get('name', '')
    return {'body': outputstyles.read(name, q.get('path') or None, _cfg(q)),
            'builtin': any(n.lower() == name.lower()
                           for n, _d in outputstyles.BUILTIN)}


def api_output_style_install(q, body):
    """Copy a claudectl starter into the user or project scope."""
    from . import outputstyles
    b = body or {}
    ok, msg = outputstyles.install_starter(
        b.get('name', ''), b.get('path') if b.get('scope') == 'project' else None,
        _cfg(b))
    return {'ok': ok, 'message': msg}


def api_output_style_select(q, body):
    from . import outputstyles
    b = body or {}
    scope_project = b.get('scope') == 'project'
    ok, msg = outputstyles.select(b.get('name', 'default'),
                                  b.get('path') if scope_project else None,
                                  _cfg(b))
    return {'ok': ok, 'message': msg}


def api_output_style_save(q, body):
    from . import outputstyles
    b = body or {}
    ok, msg = outputstyles.save(b.get('name', ''), b.get('description', ''),
                                b.get('body', ''),
                                b.get('path') if b.get('scope') == 'project'
                                else None, _cfg(b))
    return {'ok': ok, 'message': msg}


def api_output_style_delete(q, body):
    from . import outputstyles
    b = body or {}
    ok, msg = outputstyles.delete(b.get('name', ''), b.get('path') or None,
                                  _cfg(b))
    return {'ok': ok, 'message': msg}


def api_checkpoints(q, body):
    """Read-only snapshot index for one session.

    Defensive by construction: the store's naming scheme is undocumented, so
    `recognised` comes back False the moment nothing resolves and the UI hides
    the panel instead of showing a guess. See checkpoints.py.
    """
    from . import checkpoints
    folder = _folder(q.get('cfgdir'), q['enc'])
    jsonl = os.path.join(folder, f"{q['sid']}.jsonl")
    cfgdir = os.path.dirname(os.path.dirname(folder))
    try:
        return checkpoints.history(q['sid'], jsonl, cfgdir)
    except Exception as e:
        return {'recognised': False, 'files': [], 'orphans': 0,
                'store': False, 'error': str(e)}


def api_checkpoint_diff(q, body):
    from . import checkpoints
    folder = _folder(q.get('cfgdir'), q['enc'])
    cfgdir = os.path.dirname(os.path.dirname(folder))
    try:
        a, b = int(q.get('a', 1)), int(q.get('b', 2))
        return {'diff': checkpoints.diff_versions(q['sid'], q.get('file', ''),
                                                  a, b, cfgdir)}
    except Exception as e:
        return {'diff': '', 'error': str(e)}


def api_statusline(q, body):
    """Install state plus a live preview rendered from real numbers.

    The preview matters: a statusline is judged by what it looks like, and
    asking someone to install one sight-unseen into a file Claude Code owns is
    the wrong order. It is built from this project's actual memory age, pending
    lessons and today's spend, so what you see is what the session will show.
    """
    from . import statusline
    path = q.get('path') or os.getcwd()
    # git, memory, lessons and account are REAL; context and limits are
    # illustrative, because only a live session has them
    payload = {'model': {'display_name': 'Opus 5'}, 'cwd': path,
               'workspace': {'current_dir': path},
               'output_style': {'name': 'default'},
               'context_window': {'used_percentage': 42},
               'rate_limits': {'five_hour': {'used_percentage': 41},
                               'seven_day': {'used_percentage': 18}},
               'cost': {'total_cost_usd': 3.42, 'total_lines_added': 156,
                        'total_lines_removed': 23}}
    try:
        preview = statusline.plain(statusline.render(payload))
    except Exception as e:
        preview = f'(preview failed: {e})'
    accounts = [{'name': n, 'cfgdir': d, 'installed': done,
                 'blockers': [{'code': c, 'why': w} for c, w in blocked]}
                for n, d, done, blocked in statusline.by_account()]
    return {'installed': all(a['installed'] for a in accounts) if accounts else False,
            # `partial` is the state the pre-fix single-account installer left
            # behind, and the whole reason the flat bool above is not enough.
            'partial': any(a['installed'] for a in accounts)
            and not all(a['installed'] for a in accounts),
            # installed everywhere and still invisible somewhere: the second
            # half of the same lesson
            'blocked': any(a['installed'] and a['blockers'] for a in accounts),
            'accounts': accounts,
            'preview': preview, 'command': statusline._command()}


def api_statusline_set(q, body):
    """`cfgdir` targets one account; omitting it means every account, which is
    the right default — the statusline belongs to the user, not to whichever
    account happened to be active."""
    from . import statusline
    b = body or {}
    act = b.get('action', '')
    cfgdir = b.get('cfgdir') or None
    if cfgdir and cfgdir not in [d for _n, d in statusline._accounts()]:
        return {'ok': False, 'message': 'unknown account'}
    if act == 'install':
        ok, msg = statusline.install(cfgdir) if cfgdir else statusline.install_all()
    elif act == 'remove':
        ok, msg = statusline.remove(cfgdir) if cfgdir else statusline.remove_all()
    else:
        ok, msg = False, 'unknown action'
    return {'ok': ok, 'message': msg}


GET_ROUTES = {
    '/api/transcript': api_transcript,
    '/api/session/meta': api_session_meta,
    '/api/session/changed-files': api_changed_files,
    '/api/session/archived': api_archived,
    '/api/session/tags': api_tags_get,
    '/api/usage/daily': api_usage_daily,
    '/api/usage/projects': api_usage_projects,
    '/api/usage/project': api_usage_project,
    '/api/usage/plan': api_usage_plan,
    '/api/search-index': api_search_index,
    '/api/dashboard': api_dashboard,
    '/api/plugins': api_plugins,
    '/api/plugins/provenance': api_provenance,
    '/api/versions': api_versions,
    '/api/worktrees': api_worktrees,
    '/api/output-styles': api_output_styles,
    '/api/statusline': api_statusline,
    '/api/output-style/read': api_output_style_read,
    '/api/checkpoints': api_checkpoints,
    '/api/checkpoint/diff': api_checkpoint_diff,
    '/api/worktree/diff': api_worktree_diff,
    '/api/graph-lite': api_graph_lite,
    '/api/hooks': api_hooks_get,
    '/api/agents/library': api_agents_library,
    '/api/agents/read': api_agent_read,
    '/api/agents/session': api_agents_session_get,
    '/api/skills': api_skills_get,
    '/api/skills/read': api_skill_read,
    '/api/worklog': api_worklog_get,
    '/api/mcp': api_mcp_get,
    '/api/global-claude-md': api_global_claude_md,
    '/api/mcp/detail': api_mcp_detail,
    '/api/accounts': api_accounts_get,
    '/api/accounts/sync': api_accounts_sync,
    '/api/memory/state': api_memory_state,
    '/api/memory/entity': api_memory_entity,
    '/api/memory/progress': api_memory_progress,
    '/api/memory/active': api_memory_active,
    '/api/memory/auto': api_memory_auto_get,
    '/api/lessons': api_lessons_get,
    '/api/ctxaudit': api_ctxaudit,
    '/api/ctxaudit/prune-preview': api_ctxaudit_prune_preview,
    '/api/history': api_history,
    '/api/history/diff': api_history_diff,
    '/api/deny': api_deny_scan,
    '/api/logs': api_logs,
    '/api/workspace-status': api_workspace_status,
    '/api/recall-preview': api_recall_preview,
    '/api/claude-md': api_claude_md_get,
    '/api/memory-map': api_memory_map,
    '/api/system-prompt': api_system_prompt_get,
    '/api/extra-paths': api_extra_paths_get,
    '/api/add-dirs': api_add_dirs_get,
    '/api/path-complete': api_path_complete,
    '/api/health': api_health,
    '/api/brief': api_brief,
    '/api/conventions': api_conventions,
    '/api/cc-settings': api_cc_settings_get,
    '/api/automode': api_automode,
    '/api/automode/config': api_automode_config,
    '/api/client/usage': api_client_usage,
    '/api/client/project': api_client_project,
    '/api/prompt-history': api_prompt_history,
    '/api/background-agents': api_background_agents,
    '/api/disk': api_disk,
    '/api/loop-md': api_loop_md_get,
    '/api/loops': api_loops,
    '/api/omniroute/status': api_omniroute_status,
    '/api/omniroute/models': api_omniroute_models,
    '/api/plan/last': api_plan_last,
}

POST_ROUTES = {
    '/api/session/export': api_session_export,
    '/api/session/archive': api_session_archive,
    '/api/session/restore': api_session_restore,
    '/api/session/delete': api_session_delete,
    '/api/session/tags': api_tags_set,
    '/api/memory/autoscan': api_memory_autoscan,
    '/api/memory/auto': api_memory_auto_set,
    '/api/project/hide': api_project_hide,
    '/api/plugins/marketplace/add': api_plugin_marketplace_add,
    '/api/plugins/marketplace/remove': api_plugin_marketplace_remove,
    '/api/plugins/remove': api_plugin_remove,
    '/api/plugins/install': api_plugin_install,
    '/api/worktree/merge': api_worktree_merge,
    '/api/statusline': api_statusline_set,
    '/api/output-style/select': api_output_style_select,
    '/api/output-style/save': api_output_style_save,
    '/api/output-style/install': api_output_style_install,
    '/api/output-style/delete': api_output_style_delete,
    '/api/hooks/template': api_hooks_template,
    '/api/hooks/remove': api_hooks_remove,
    '/api/hooks/toggle': api_hooks_toggle,
    '/api/hooks/purge': api_hooks_purge,
    '/api/agents/create': api_agent_create,
    '/api/agents/delete': api_agent_delete,
    '/api/agents/session': api_agents_session,
    '/api/skills/install': api_skill_install,
    '/api/skills/remove': api_skill_remove,
    '/api/skills/create': api_skill_create,
    '/api/worklog': api_worklog_set,
    '/api/memory/toggles': api_memory_toggles,
    '/api/mcp/add': api_mcp_add,
    '/api/global-claude-md': api_global_claude_md_save,
    '/api/skills/library': api_skills_library,
    '/api/mcp/remove': api_mcp_remove,
    '/api/accounts/action': api_accounts_post,
    '/api/accounts/terminal': api_accounts_terminal,
    '/api/lessons': api_lessons_post,
    '/api/ctxaudit/prune': api_ctxaudit_prune,
    '/api/history/restore': api_history_restore,
    '/api/ctxaudit/compact': api_ctxaudit_compact,
    '/api/ctxaudit/protect': api_ctxaudit_protect,
    '/api/deny/apply': api_deny_apply,
    '/api/claude-md/scaffold': api_claude_md_scaffold,
    '/api/open-editor': api_open_editor,
    '/api/system-prompt': api_system_prompt_set,
    '/api/extra-paths': api_extra_paths_set,
    '/api/add-dirs': api_add_dirs_set,
    '/api/open-path': api_open_path,
    '/api/health/allowlist': api_health_allowlist,
    '/api/conventions/sync': api_conventions_sync,
    '/api/conventions/pin': api_conventions_pin,
    '/api/brief/dismiss': api_brief_dismiss,
    '/api/cc-settings': api_cc_settings_set,
    '/api/automode': api_automode_set,
    '/api/disk/gc': api_disk_gc,
    '/api/loop-md': api_loop_md_set,
    '/api/loops/start': api_loop_start,
    '/api/loops/stop': api_loop_stop,
    '/api/inject/launch': api_inject_launch,
    '/api/job': api_job_start,
    '/api/plan/edit': api_plan_edit,   # TUI-only by design: a terminal cannot free-text-edit a plan, so it needs structured Edit/Delete/Insert/Move. The GUI's plan editor is a <textarea> that does all four natively.
}
