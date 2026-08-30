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


def _run_cancellable(cmd, input_text=None, capture_output=True, text=True,
                     encoding='utf-8', errors='ignore', cwd=None, env=None,
                     timeout=600):
    """subprocess.run replacement that honours the current job's cancel_event.

    Returns stdout string (or '' on cancel/failure). Raises JobCancelled if
    the user cancels while the subprocess is running."""
    job = getattr(_JOBCTX, 'job', None)
    if job and job.get('cancel_event', threading.Event()).is_set():
        raise JobCancelled
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
            if job is not None:
                job['last_subprocess_error'] = {'code': proc.returncode, 'output': stdout}
            return ''
        return stdout
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        if job is not None:
            job.setdefault('messages', []).append(
                {'ok': False, 'text': 'timed out after %ss — upstream may be an '
                                      'unresponsive OmniRoute/failover endpoint'
                                      % timeout})
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
    from . import ui, diffview, claude_md, hooks

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
    # hooks.py imports text_input by value at module top
    hooks.text_input = text_input

    _orig_ui_confirm = ui.confirm
    def ui_confirm(prompt, danger=False):
        job = getattr(_JOBCTX, 'job', None)
        if job is None:
            return _orig_ui_confirm(prompt, danger=danger)
        return True     # GUI flows pre-confirm destructive actions client-side
    ui.confirm = ui_confirm


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


def _refresh_project(path, folder, auto_cap=6):
    """Run one incremental memory refresh in-process under the scan-lock so the
    badge and /api/memory/active reflect it. Silent (headless Claude calls).
    Returns True if it actually ran (acquired the lock)."""
    from . import memory
    if not memory.acquire_scan_lock(path):
        return False                      # another refresh already in flight
    memory._tls.silent = True
    try:
        name = os.path.basename(path.rstrip('\\/')) or path
        # auto_cycle, not refresh_memory: "auto memory" means every memory
        # surface, lessons included. See memory.auto_cycle.
        memory.auto_cycle(path, folder, name, auto_cap=auto_cap)
    except Exception:
        _c.log.exception('gui: memory refresh failed for %s', path)
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
    from .config import load_settings
    from . import gui
    pd = load_settings().get('project_defaults') or {}
    out = []
    for p in gui.list_projects():
        if (pd.get(p['encoded']) or {}).get('auto_memory'):
            out.append((p['path'],
                        _store.project_folder(p['primary_cfgdir'], p['encoded']),
                        p['encoded']))
    return out


def _auto_scan_pass():
    """One sweep: refresh each opted-in project whose source changed and that
    isn't already updating. Cheap (hash-only) staleness gate keeps token cost
    to genuinely-changed projects."""
    from . import memory
    for path, folder, _enc in _auto_projects():
        try:
            if memory.scan_lock_status(path) is not None:
                continue                                  # already running
            if not memory.is_stale(path, folder):
                continue                                  # nothing changed
            _refresh_project(path, folder, auto_cap=6)    # blocking, sequential
        except Exception:
            _c.log.exception('gui: auto-scan pass failed for %s', path)


def start_auto_memory_scheduler():
    """Daemon thread: one pass on GUI start, then every auto_memory_interval
    seconds. Started by the real GUI entry points only (never make_server, so
    tests don't spawn refreshes). Idempotent."""
    global _sched_started
    if _sched_started:
        return
    _sched_started = True
    import threading
    from .config import load_settings
    _sched_stop.clear()

    def _loop():
        # wait(), not sleep(): server_close() must be able to end this, and a
        # thread parked in sleep(3600) cannot be told anything
        if _sched_stop.wait(2):           # let the server settle first
            return
        while not _sched_stop.is_set():
            try:
                _auto_scan_pass()
            except Exception:
                _c.log.exception('gui: auto-memory scheduler tick failed')
            try:
                interval = max(60, int(load_settings().get('auto_memory_interval', 3600)))
            except Exception:
                interval = 3600
            _sched_stop.wait(interval)

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


#: checked by NAME, wherever they appear. These three are the ones that reach
#: the filesystem; `path` is deliberately absent, because several endpoints
#: legitimately take a directory that does not exist yet, and every path that
#: becomes a subprocess cwd already goes through paths.resolve_dir.
PARAM_CHECKS = {
    'enc': lambda v: _store.is_encoded(v),
    'sid': lambda v: _store.is_encoded(v),
    'cfgdir': _cfgdir_ok,
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
    'enc', 'sid', 'cfgdir', 'path', 'action', 'kind', 'name', 'id',
    'dir', 'file', 'key', 'event', 'scope', 'text', 'value', 'model', 'task',
    'url', 'query', 'q',
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
    from .session_menu import _arch_of
    from .sessions import scan_sessions, load_name, format_age, get_session_stats
    from .gui import _used_omni
    folder = _arch_of(_folder(q.get('cfgdir'), q['enc']))
    out = []
    for mtime, sid, preview, count in scan_sessions(folder):
        omni = False
        try:
            omni = _used_omni(get_session_stats(os.path.join(folder, f'{sid}.jsonl')))
        except Exception:
            pass
        out.append({'sid': sid, 'title': load_name(folder, sid) or '',
                    'preview': preview, 'age': format_age(mtime).strip(),
                    'count': count, 'omni': omni})
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
            'accounts': [{'name': n, 'dir': dd,
                          'count': sum(len(b or []) for b in (a.get('hooks') or {}).values())}
                         for n, dd, a in per],
            'templates': [{'key': k, 'desc': v.get('desc', ''),
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
    mine = []
    for scope, d in (('user', user_agents_dir()),
                     ('project', project_agents_dir(q['path']) if q.get('path') else None)):
        if not d:
            continue
        for n, desc, model, path in list_agents(d):
            mine.append({'name': n, 'desc': (desc or '')[:140], 'model': model,
                         'path': path, 'scope': scope})
    from .agents import KNOWN_TOOLS
    from .config import models
    vals, labels = models()
    return {'categories': cats, 'own': mine,
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
    write_agent(p, {'name': body['name'],
                    'description': body.get('description', ''),
                    **({'tools': body['tools']} if body.get('tools') else {}),
                    **({'model': body['model']} if body.get('model') else {})},
                body.get('body', ''))
    return {'ok': True, 'file': p}


def api_agent_delete(q, body):
    try:
        os.remove(body['file'])
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
    return {'refs': load_session_agents(folder).get('__project__', []),
            'suggested': suggested, 'limit': SAFE_AGENT_LIMIT}


def api_agents_session(q, body):
    from .agents import sync_project_agents
    from .sessions import save_session_agents
    refs = body.get('refs', [])
    folder = _folder(body.get('cfgdir'), body['enc'])
    if os.path.isdir(folder):
        save_session_agents(folder, '__project__', refs)
    n = sync_project_agents(body['path'], refs)
    return {'ok': True, 'active': n}


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
    return {'suggestions': [{'tag': t, 'text': x}
                            for t, x in brief.work_suggestions(q['path'], folder)],
            # structured, so the GUI can collapse per repo and show counts.
            # `since_last` stays for any client still reading the flat lines.
            'since': diff,
            'since_last': brief.session_diff(q['path'], folder)}


def api_conventions(q, body):
    """Conventions shared across projects, and the global CLAUDE.md block they
    would become."""
    from . import conventions
    return {'conventions': conventions.collect_conventions(),
            'block': conventions.build_block()}


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
    return {'on': on, 'installed': hooks.worklog_hook_installed(),
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
    from .skills import list_templates, list_skills, project_skills_dir
    templates = [{'name': n, 'desc': (d or '')[:160], 'dir': sd, 'source': src}
                 for n, d, sd, src in list_templates()]
    project = []
    if q.get('path'):
        project = [{'name': n, 'desc': (d or '')[:160], 'dir': sd}
                   for n, d, sd in list_skills(project_skills_dir(q['path']))]
    return {'templates': templates, 'project': project}


def api_skill_read(q, body):
    from .skills import parse_skill
    meta, body_txt = parse_skill(q['dir'])
    return {'meta': meta, 'body': body_txt}


def api_skill_install(q, body):
    from .skills import install_skill
    dest = install_skill(body.get('dir', ''), body.get('path', ''))
    return {'ok': bool(dest), 'dir': dest}


def api_skill_remove(q, body):
    from .skills import delete_skill
    return {'ok': delete_skill(body.get('dir', ''))}


def api_skill_create(q, body):
    from .skills import write_skill, project_skills_dir, library_dir, _slug
    base = project_skills_dir(body['path']) if body.get('path') else library_dir()
    skill_dir = os.path.join(base, _slug(body['name']))
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
    from .mcp import get_mcp_status
    return {'servers': [{'name': n, 'status': s}
                        for n, s in get_mcp_status(q.get('cfgdir'))]}


def api_skills_library(q, body):
    """Copy a template or project skill into the user's own library."""
    from .skills import save_to_library
    dest = save_to_library(body['dir'])
    return {'ok': bool(dest), 'dir': dest,
            'error': '' if dest else 'not a skill folder'}


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
    from .memhub import _state
    from .lessons import pending_sids
    folder = _folder(q.get('cfgdir'), q['enc'])
    st = _state(q['path'], folder)
    mem = st['mem']
    try:
        n_unscanned = len(pending_sids(folder, mem))
    except Exception:
        n_unscanned = 0
    return {'generated_at': mem.get('generated_at', ''),
            'n_entities': len(st['entities']),
            'n_lessons': len(st['lessons']),
            'n_pending': len(st['pending']),
            'n_unscanned': n_unscanned,
            'hook_on': st['hook_on'], 'rules_on': st['rules_on'],
            'budget': (st['settings'] or {}).get('memory_budget', 600),
            'est': st['est']}


def api_memory_progress(q, body):
    from .memory import scan_lock_status
    return {'progress': scan_lock_status(q['path'])}


def api_memory_autoscan(q, body):
    """Called each time a project is opened. Kick off an in-process memory
    refresh ONLY when the project's source has actually changed (cheap
    hash-only `is_stale` check) — so revisiting an up-to-date project neither
    re-scans nor flashes the badge. Returns whether a refresh is now running."""
    from .config import load_settings
    from . import memory
    path = body.get('path', '')
    folder = _folder(body.get('cfgdir'), body.get('enc', ''))
    if not path or not folder:
        return {'running': False, 'stale': False}
    running = memory.scan_lock_status(path) is not None
    if running:
        return {'running': True, 'stale': True}
    try:
        st = load_settings()
        force = bool(body.get('force'))
        on_open = st.get('memory_auto_refresh') == 'open'
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


def api_lessons_get(q, body):
    from .memory import load_memory
    mem = load_memory(q['path'], _folder(q.get('cfgdir'), q['enc']))
    lessons = [e for e in mem.get('entities', []) if e.get('type') == 'lesson']
    lessons.sort(key=lambda e: (e.get('status') != 'pending',
                                -e.get('confidence', 0)))
    return {'lessons': [{'id': e.get('id'), 'name': e.get('name', ''),
                         'summary': e.get('summary', ''),
                         'status': e.get('status', 'pending'),
                         'kind': e.get('kind', ''),
                         'confidence': e.get('confidence', 0)}
                        for e in lessons]}


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


def api_ctxaudit_prune(q, body):
    from .claude_md import prune_claude_md
    old_tok, new_tok = prune_claude_md(body['path'],
                                       _folder(body.get('cfgdir'), body['enc']))
    return {'ok': True, 'old_tokens': old_tok, 'new_tokens': new_tok}


def api_ctxaudit_compact(q, body):
    from .ctxaudit import append_compact_section
    return {'ok': bool(append_compact_section(body['path']))}


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
    from .workspace import _status_lines
    lines, _m, score, safe = _status_lines(q['path'],
                                           _folder(q.get('cfgdir'), q['enc']))
    from .render import strip_ansi
    return {'lines': [strip_ansi(l) for l in lines], 'score': score, 'safe': safe}


def api_recall_preview(q, body):
    from .recall import retrieve
    from .config import load_settings
    budget = load_settings().get('memory_budget', 600)
    r = retrieve(q['path'], _folder(q.get('cfgdir'), q['enc']),
                 q.get('q', ''), budget_tokens=budget)
    return {'context': r.get('text', ''), 'tokens': r.get('tokens', 0),
            'empty': r.get('empty', True)}


# ── CLAUDE.md, system prompt, memory map ─────────────────────

def api_claude_md_get(q, body):
    p = os.path.join(q['path'], 'CLAUDE.md')
    try:
        text = open(p, encoding='utf-8', errors='ignore').read()
    except Exception:
        text = ''
    return {'text': text, 'exists': bool(text)}


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


# ── inject-context & plan-execute ────────────────────────────

def api_inject_sessions(q, body):
    from .context_inject import find_sessions_across_accounts
    from .sessions import format_age
    out = []
    for acct, folder, sid, mtime, preview, title in \
            find_sessions_across_accounts(q['path']):
        out.append({'account': acct, 'folder': folder, 'sid': sid,
                    'age': format_age(mtime).strip(),
                    'title': title or preview or sid[:8]})
    return {'sessions': out}


def api_inject_launch(q, body):
    """Write the context file and launch a new session in a new console
    under the chosen account (mirror of context_inject.run minus menus)."""
    import subprocess
    from .context_inject import _write_context_file, CTX_FILE
    from .config import get_claude_exe, launch_defaults
    from .sessions import load_add_dirs, read_extra_paths
    from .paths import encode_component, resolve_dir

    path = body['path']
    if not resolve_dir(path):       # becomes a subprocess cwd below
        return {'ok': False, 'error': 'not a directory: %s' % (path or '(empty)')}
    ctx_path, title = _write_context_file(path, body['folder'], body['sid'],
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
        from .agents import _new_agent_ai
        jid = start_job('Generating agent', lambda: _new_agent_ai(path or None),
                        inputs=[body.get('description', '')])
    elif kind == 'hook_ai':
        from .hooks import _ai_hook
        jid = start_job('Generating hook', lambda: _ai_hook(),
                        inputs=[body.get('description', '')])
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
                raise RuntimeError('No output from Claude')
            if not _pager_confirm(f'SKILL / {skills._slug(sk_name)} — approve to write',
                                  content):
                return {'ok': False, 'rejected': True}
            d = skills.write_skill_raw(proj, sk_name, content)
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
        from .config import load_settings, provider_env
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
        # else a stale provider_exec_model default silently routes every
        # council call at an unreachable proxy, _headless swallows the
        # errors, and optimize_plan_council quietly no-ops the plan back
        # unchanged with no error shown.
        via = body.get('via', 'anthropic')
        omni_env = provider_env(s, model='_') if via == 'provider' else {}

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
        from .config import load_settings, provider_env, config_dir
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
            omni_env = provider_env(s, model='_') if via == 'provider' else {}
            # write user-edited plan text before launching
            if plan_text:
                write_plan_file(path, task, plan_text)
            if body.get('model'):
                model = body['model']
            elif omni_env:
                model = s.get('provider_exec_model') or omniroute.AUTO_MODEL
            else:
                model = s.get('exec_model', '')
            if omni_env:
                # ONE seam: reachability, daemon start, failover proxy, model
                # validation and the context advisory all live in
                # prepare_launch. This used to be forty lines duplicated from
                # plan_execute.run(), and the two copies had already drifted.
                from .plan_execute import context_bytes
                _pv_env, _warn = omniroute.prepare_launch(
                    model, s, ctx_bytes=context_bytes(path, plan_text))
                omni_env.update(_pv_env)
                if _warn:
                    ui.flash(_warn, ok=False, secs=3)
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
        proj = path or None

        def _install():
            exec_model = load_settings().get('provider_exec_model', '')
            ok, msg = skills.install_from_git(url, proj, exec_model)
            if not ok:
                raise RuntimeError(msg)
            return {'message': msg}
        jid = start_job(f'Installing from {url}', _install)
    elif kind == 'gateway_ensure':
        from . import gateway

        def _gwup():
            ok, msg = gateway.ensure_running()
            return {'ok': ok, 'message': msg}
        jid = start_job('Starting gateway', _gwup)
    elif kind == 'gateway_stop':
        from . import gateway

        def _gwdown():
            ok, msg = gateway.stop_running()
            return {'ok': ok, 'message': msg}
        jid = start_job('Stopping gateway', _gwdown)
    elif kind == 'provider_ensure':
        from . import omniroute
        from .config import load_settings

        def _ensure():
            s = load_settings()
            ok, msg = omniroute.ensure_running(s.get('provider_base_url', ''))
            return {'ok': ok, 'message': msg}
        jid = start_job('Starting OmniRoute', _ensure)
    elif kind == 'provider_probe':
        from . import omniroute
        from .config import load_settings

        def _probe():
            s = load_settings()
            base, key = s.get('provider_base_url', ''), s.get('provider_api_key', '')
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
    elif kind == 'provider_test_connection':
        from . import omniroute
        conn_id = body.get('conn_id', '')

        def _test():
            ok, msg = omniroute.cli_test_connection(conn_id)
            return {'ok': ok, 'message': msg}
        jid = start_job(f'Testing {conn_id}', _test)
    elif kind == 'provider_live_test':
        from . import omniroute
        from .config import load_settings
        model = body.get('model') or omniroute.AUTO_MODEL

        def _live():
            s = load_settings()
            ok, used, msg = omniroute.test_live(
                s.get('provider_base_url', ''), model, s.get('provider_api_key', ''))
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

def api_provider_status(q, body):
    """Reachability plus, for OmniRoute only, its circuit-breaker detail.

    Branching on kind is not a shortcut — a generic Anthropic-shaped server has
    no /v1/models catalogue and no provider-health endpoint, so the OmniRoute
    path would report a perfectly working Ollama as "not running". A plain
    reachability dot is the honest amount of signal available."""
    from . import gateway, omniroute
    from .config import load_settings
    s = load_settings()
    kind = s.get('provider_kind') or ''
    gw = {'kind': s.get('gateway_kind') or '',
          'target': s.get('gateway_target_base_url') or ''}
    if gw['kind']:
        gw['error'] = gateway.target_error(gw['target'])
        gw['running'] = gateway._D.is_ready(int(s.get('gateway_port') or 20130))
    if kind != 'omniroute':
        base = s.get('provider_base_url', '')
        return {'kind': kind, 'gateway': gw,
                'exec_model': s.get('provider_exec_model', ''),
                # With a gateway in front, the reachable thing IS the gateway --
                # the OpenAI-shaped host behind it cannot answer a probe shaped
                # like this one.
                'reachable': bool(gw.get('running')) if gw['kind']
                             else (bool(base) and omniroute.is_reachable(
                                 base, s.get('provider_api_key', ''))),
                'providers': [], 'lockouts': [], 'connections': [],
                'model_count': 0, 'usable_count': 0}
    base, key = s.get('provider_base_url', ''), s.get('provider_api_key', '')
    # ONE concurrent fetch of both payloads, then everything is derived locally.
    # Each OmniRoute round trip costs ~2s on a loaded instance, so the previous
    # five serial calls made this handler a ~13s page stall.
    entries, h = omniroute.fetch_both(base, key)
    summary = h.get('summary') or {}
    usable, _autos, _ex = omniroute.classify_models(entries, h)
    return {
        'kind': kind,
        'gateway': gw,
        'exec_model': s.get('provider_exec_model', ''),
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


def api_provider_models(q, body):
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
    base = s.get('provider_base_url', '')
    key = s.get('provider_api_key', '')

    if (s.get('provider_kind') or '') != 'omniroute':
        # No catalogue exists for a generic Anthropic-shaped server or an
        # OpenAI-shaped host behind the gateway. Offer the model the user
        # configured and nothing else -- inventing a list here would be offering
        # ids that 401 on the first turn, which is the exact failure the
        # OmniRoute filtering below exists to prevent.
        cur = s.get('provider_exec_model', '')
        return {'models': [cur] if cur else [], 'labels': {cur: cur} if cur else {},
                'usable': [], 'excluded': {}, 'filtered': False,
                'kind': s.get('provider_kind') or ''}

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
    from . import outputstyles
    path = q.get('path') or None
    return {'styles': outputstyles.listing(path, _cfg(q)),
            'active': outputstyles.current(path, _cfg(q))}


def api_output_style_read(q, body):
    from . import outputstyles
    return {'body': outputstyles.read(q.get('name', ''), q.get('path') or None,
                                      _cfg(q))}


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
    '/api/memory/progress': api_memory_progress,
    '/api/memory/active': api_memory_active,
    '/api/memory/auto': api_memory_auto_get,
    '/api/lessons': api_lessons_get,
    '/api/ctxaudit': api_ctxaudit,
    '/api/deny': api_deny_scan,
    '/api/workspace-status': api_workspace_status,
    '/api/recall-preview': api_recall_preview,
    '/api/claude-md': api_claude_md_get,
    '/api/memory-map': api_memory_map,
    '/api/system-prompt': api_system_prompt_get,
    '/api/extra-paths': api_extra_paths_get,
    '/api/add-dirs': api_add_dirs_get,
    '/api/path-complete': api_path_complete,
    '/api/inject/sessions': api_inject_sessions,
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
    '/api/provider/status': api_provider_status,
    '/api/provider/models': api_provider_models,
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
    '/api/plugins/marketplace/add': api_plugin_marketplace_add,
    '/api/plugins/marketplace/remove': api_plugin_marketplace_remove,
    '/api/plugins/remove': api_plugin_remove,
    '/api/plugins/install': api_plugin_install,
    '/api/worktree/merge': api_worktree_merge,
    '/api/statusline': api_statusline_set,
    '/api/output-style/select': api_output_style_select,
    '/api/output-style/save': api_output_style_save,
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
    '/api/ctxaudit/compact': api_ctxaudit_compact,
    '/api/deny/apply': api_deny_apply,
    '/api/claude-md/scaffold': api_claude_md_scaffold,
    '/api/open-editor': api_open_editor,
    '/api/system-prompt': api_system_prompt_set,
    '/api/extra-paths': api_extra_paths_set,
    '/api/add-dirs': api_add_dirs_set,
    '/api/open-path': api_open_path,
    '/api/health/allowlist': api_health_allowlist,
    '/api/conventions/sync': api_conventions_sync,
    '/api/cc-settings': api_cc_settings_set,
    '/api/automode': api_automode_set,
    '/api/disk/gc': api_disk_gc,
    '/api/loop-md': api_loop_md_set,
    '/api/inject/launch': api_inject_launch,
    '/api/job': api_job_start,
    '/api/plan/edit': api_plan_edit,   # TUI-only by design: a terminal cannot free-text-edit a plan, so it needs structured Edit/Delete/Insert/Move. The GUI's plan editor is a <textarea> that does all four natively.
}
