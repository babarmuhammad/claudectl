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
import threading
import time
import uuid

from . import config as _c


# ── job model ────────────────────────────────────────────────

_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBCTX = threading.local()          # .job set on job threads


def _job(jid):
    with _JOBS_LOCK:
        return _JOBS.get(jid)


def start_job(label, fn, inputs=None):
    """Run fn() on a daemon thread under the UI bridge. Returns job id.
    inputs: queued answers for any text_input the flow asks for."""
    jid = uuid.uuid4().hex[:12]
    job = {'id': jid, 'status': 'running', 'label': label, 'messages': [],
           'result': None, 'error': '', 'gate': None,
           'decision': None, 'decision_evt': threading.Event(),
           'inputs': list(inputs or []), 'started': time.time(),
           'cancelled': False}
    with _JOBS_LOCK:
        _JOBS[jid] = job

    def _run():
        from . import memory
        _JOBCTX.job = job
        memory._tls.silent = True
        try:
            job['result'] = fn()
            if job['status'] == 'running':
                job['status'] = 'done'
        except Exception as e:
            _c.log.exception('gui job failed: %s', label)
            job['error'] = str(e)
            job['status'] = 'error'
        finally:
            _JOBCTX.job = None

    threading.Thread(target=_run, daemon=True).start()
    return jid


def job_status(jid):
    job = _job(jid)
    if not job:
        return None
    out = {k: job[k] for k in ('id', 'status', 'label', 'messages', 'error')}
    out['elapsed'] = int(time.time() - job['started'])
    if job['status'] == 'awaiting' and job['gate']:
        g = job['gate']
        out['gate'] = {'title': g['title'], 'diff': g['diff'],
                       'old_len': len(g.get('old', '')), 'new_len': len(g.get('new', ''))}
    if job['status'] == 'done':
        out['result'] = _jsonable(job['result'])
    return out


def job_decide(jid, apply):
    """Answer a pending confirm gate."""
    job = _job(jid)
    if not job or job['status'] != 'awaiting':
        return False
    job['decision'] = bool(apply)
    job['status'] = 'running'
    job['decision_evt'].set()
    return True


def job_cancel(jid):
    job = _job(jid)
    if not job:
        return False
    if job['status'] == 'awaiting':      # a cancel at the gate = reject
        return job_decide(jid, False)
    job['cancelled'] = True
    job['status'] = 'cancelled'
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
        return _gate(job, title, '', content, content)
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


def _gate(job, title, old, new, diff):
    """Park the job until the GUI approves/rejects the proposed content."""
    job['gate'] = {'title': title, 'old': old, 'new': new, 'diff': diff}
    job['decision'] = None
    job['decision_evt'].clear()
    job['status'] = 'awaiting'
    job['decision_evt'].wait(timeout=3600)
    job['gate'] = None
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
        memory.refresh_memory(path, folder, name, auto_cap=auto_cap)
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
                        os.path.join(p['primary_cfgdir'], 'projects', p['encoded']),
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

    def _loop():
        import time as _t
        _t.sleep(2)                       # let the server settle before first pass
        while True:
            try:
                _auto_scan_pass()
            except Exception:
                _c.log.exception('gui: auto-memory scheduler tick failed')
            interval = load_settings().get('auto_memory_interval', 3600)
            try:
                _t.sleep(max(60, int(interval)))
            except Exception:
                _t.sleep(3600)

    threading.Thread(target=_loop, daemon=True).start()


# ── shared helpers ───────────────────────────────────────────

def _entries():
    """[(mtime, path, enc, cfgdir)] across accounts — same shape main.run
    and the stats screens consume."""
    from .paths import find_actual_path
    out = []
    for _name, acct_dir in _c.all_config_dirs():
        pdir = os.path.join(acct_dir, 'projects')
        if not os.path.isdir(pdir):
            continue
        for enc in os.listdir(pdir):
            proj = os.path.join(pdir, enc)
            if not os.path.isdir(proj):
                continue
            actual = find_actual_path(enc)
            if actual:
                out.append((os.path.getmtime(proj), actual, enc, acct_dir))
    out.sort(reverse=True)
    return out


def _folder(cfgdir, enc):
    return os.path.join(cfgdir or _c.config_dir, 'projects', enc)


# ── sessions & transcript ────────────────────────────────────

def api_transcript(q, body):
    from .transcript import iter_transcript
    jsonl = os.path.join(_folder(q.get('cfgdir'), q['enc']), f"{q['sid']}.jsonl")
    return {'messages': iter_transcript(jsonl)}


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
    from .sessions import scan_sessions, load_name, format_age
    folder = _arch_of(_folder(q.get('cfgdir'), q['enc']))
    out = []
    for mtime, sid, preview, count in scan_sessions(folder):
        out.append({'sid': sid, 'title': load_name(folder, sid) or '',
                    'preview': preview, 'age': format_age(mtime).strip(),
                    'count': count})
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
        usage_mod.refresh_now()
    else:
        usage_mod._ensure_started()
    out = []
    with usage_mod._lock:
        state = dict(usage_mod._acct_state)
    for d, st in state.items():
        data = st.get('data')
        wins = usage_mod._extract_windows(data) if data else []
        out.append({'account': st.get('name', os.path.basename(d)),
                    'email': st.get('email', ''),
                    'windows': [{'label': l, 'pct': p,
                                 'resets': usage_mod._fmt_reset(r) if r else ''}
                                for l, p, r in wins]})
    return {'accounts': out}


def api_search_index(q, body):
    from .search import build_search_index
    rows, partial = build_search_index(_entries())
    return {'rows': rows, 'partial': partial}


# ── managers: hooks / agents / mcp / accounts ────────────────

def api_hooks_get(q, body):
    from . import hooks
    d = hooks._load()
    out = []
    for event, block in (d.get('hooks') or {}).items():
        for i, entry in enumerate(block if isinstance(block, list) else []):
            out.append({'event': event, 'index': i,
                        'label': hooks._hook_label(entry),
                        'matcher': entry.get('matcher', '')})
    active = {h['label'] for h in out}
    return {'hooks': out,
            'templates': [{'key': k, 'desc': v.get('desc', ''),
                           'installed': k in active}
                          for k, v in hooks.TEMPLATES.items()]}


def api_hooks_template(q, body):
    from . import hooks
    t = hooks.TEMPLATES.get(body['key'])
    if not t:
        return {'ok': False, 'error': 'unknown template'}
    d = hooks._load()
    hooks_d = d.setdefault('hooks', {})
    block = hooks_d.setdefault(t['event'], [])
    block.append(t['entry'])
    hooks._save(d)
    return {'ok': True}


def api_hooks_remove(q, body):
    from . import hooks
    d = hooks._load()
    block = (d.get('hooks') or {}).get(body['event'])
    i = int(body['index'])
    if not isinstance(block, list) or i >= len(block):
        return {'ok': False, 'error': 'not found'}
    block.pop(i)
    if not block:
        d['hooks'].pop(body['event'], None)
    hooks._save(d)
    return {'ok': True}


def api_hooks_purge(q, body):
    from . import hooks
    d = hooks._load()
    removed = 0
    for event in list((d.get('hooks') or {})):
        block = d['hooks'][event]
        keep = [e for e in block
                if not any(hooks._is_broken(c) for c in hooks._entry_commands(e))]
        removed += len(block) - len(keep)
        if keep:
            d['hooks'][event] = keep
        else:
            d['hooks'].pop(event)
    hooks._save(d)
    return {'ok': True, 'removed': removed}


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
    return {'categories': cats, 'own': mine}


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
    from .config import load_settings, save_settings
    from . import hooks
    enc = body.get('enc', '')
    on = bool(body.get('on'))
    s = load_settings()
    s.setdefault('project_defaults', {}).setdefault(enc, {})['worklog'] = on
    save_settings(s)
    if on:
        hooks.install_worklog_hook()     # ensure the global hook exists
    return {'ok': True, 'on': on, 'installed': hooks.worklog_hook_installed()}


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


def api_mcp_get(q, body):
    from .mcp import get_mcp_status
    return {'servers': [{'name': n, 'status': s} for n, s in get_mcp_status()]}


def api_mcp_remove(q, body):
    from .config import get_claude_exe
    import subprocess
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    p = subprocess.run([exe, 'mcp', 'remove', body['name'],
                        '-s', body.get('scope', 'local')],
                       capture_output=True, text=True, timeout=30)
    return {'ok': p.returncode == 0, 'error': (p.stderr or '').strip()}


def api_mcp_add(q, body):
    from .config import get_claude_exe
    import subprocess
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    args = [exe, 'mcp', 'add', body['name']]
    if body.get('transport') in ('sse', 'http'):
        args += ['--transport', body['transport'], body['url']]
    else:
        args += ['--', *str(body.get('command', '')).split()]
    if body.get('scope'):
        args[3:3] = ['-s', body['scope']]
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    return {'ok': p.returncode == 0, 'error': (p.stderr or '').strip()}


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


def api_accounts_terminal(q, body):
    """login / parallel — spawn a terminal for the account (argv-list form)."""
    import subprocess
    from .accounts import _env_for
    from .config import get_claude_exe
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    name = body.get('name', 'claude')
    subprocess.Popen(['cmd', '/c', 'start', f'claude [{name}]', 'cmd', '/c', exe],
                     env=_env_for(body.get('dir', '')))
    return {'ok': True}


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
    from .paths import encode_component
    raw = (body.get('path') or '').strip()
    cand = os.path.abspath(os.path.expandvars(os.path.expanduser(raw))) if raw else ''
    if not cand or not os.path.isdir(cand):
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
    from .config import get_claude_exe, load_settings
    from .sessions import load_add_dirs, read_extra_paths
    from .paths import encode_component

    path = body['path']
    ctx_path, title = _write_context_file(path, body['folder'], body['sid'],
                                          body.get('account', 'default'))
    exe = get_claude_exe()
    if not exe:
        return {'ok': False, 'error': 'claude.exe not found'}
    target_dir = body.get('target_cfgdir') or _c.config_dir
    target_folder = os.path.join(target_dir, 'projects', encode_component(path))
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
    model = load_settings().get('default_model', '')
    if model:
        args += ['--model', model]
    sp = os.path.join(target_folder, 'system-prompt.txt')
    if os.path.isfile(sp):
        args += ['--system-prompt-file', sp]
    add_dirs = [d for d in load_add_dirs(target_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]
    title_arg = f'claude — {os.path.basename(path) or path}'
    subprocess.Popen(['cmd', '/c', 'start', title_arg, 'cmd', '/c'] + args,
                     cwd=path, env=env)
    return {'ok': True}




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
        def _an():
            doc = analyze_mcp_tools(mcp_name)
            if not doc:
                raise RuntimeError('No output from Claude — MCP may need authentication')
            return {'written': update_global_claude_md_mcp(mcp_name, doc)}
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
        # council must route through the SAME channel the user picked for
        # execution (body['via']), not the account-wide default setting --
        # else a stale omniroute_exec_model default silently routes every
        # council call at an unreachable proxy, _headless swallows the
        # errors, and optimize_plan_council quietly no-ops the plan back
        # unchanged with no error shown.
        via = body.get('via', 'anthropic')
        omni_env = omniroute_env(s) if via == 'omniroute' else {}

        def _make():
            plan = _plan(task, model, path, effort)
            if not plan:
                raise RuntimeError('Planning failed or produced no output')
            if council:
                plan = optimize_plan_council(task, plan, path, omni_env=omni_env)
            plan_path = write_plan_file(path, task, plan)
            if not plan_path:
                raise RuntimeError('Could not save plan file')
            return {'plan': plan, 'plan_path': plan_path}
        jid = start_job(f'Writing plan ({model}){" + council" if council else ""}', _make)
    elif kind == 'plan_launch':
        from .plan_execute import build_exec_launch, write_plan_file
        from .config import load_settings, omniroute_env, config_dir
        from . import omniroute, ui
        task = body.get('task', '')
        plan_text = body.get('plan_text', '')
        per_step = bool(body.get('per_step'))
        cfgdir = body.get('account') or ''
        exec_folder = folder
        if cfgdir and cfgdir != config_dir:
            from .paths import encode_component
            exec_folder = os.path.join(cfgdir, 'projects', encode_component(path))

        def _launch():
            import subprocess
            s = load_settings()
            via = body.get('via', 'anthropic')
            omni_env = omniroute_env(s) if via == 'omniroute' else {}
            # write user-edited plan text before launching
            if plan_text:
                write_plan_file(path, task, plan_text)
            if omni_env:
                ok, msg = omniroute.ensure_running(s.get('omniroute_base_url', ''))
                ui.flash(f'OmniRoute: {msg}', ok=ok)
                if not ok:
                    raise RuntimeError(msg)
            if body.get('model'):
                model = body['model']
            elif omni_env:
                model = s.get('omniroute_exec_model') or omniroute.AUTO_MODEL
            else:
                model = s.get('exec_model', '')
            args, env = build_exec_launch(path, exec_folder, task, model, omni_env, cfgdir)
            if not args:
                raise RuntimeError('claude.exe not found')
            title = f"claude — {os.path.basename(path)}"
            subprocess.Popen(['cmd', '/c', 'start', title, 'cmd', '/c'] + args,
                             cwd=path, env=env)
            return {'model': model, 'via': via}
        jid = start_job('Launching execute session' + (' (per-step)' if per_step else ''), _launch)
    elif kind == 'plan_replan':
        from .plan_execute import replan_from_plan
        task = body.get('task', '')
        plan_text = body.get('plan_text', '')
        feedback = body.get('feedback', '')
        model = body.get('model') or 'claude-sonnet-5'
        effort = body.get('effort', '')

        def _replan():
            revised = replan_from_plan(plan_text or task, feedback, model, path, effort)
            if not revised:
                raise RuntimeError('Re-plan failed or produced no output')
            return {'plan': revised}
        jid = start_job('Re-planning with feedback', _replan)
    elif kind == 'skill_git_install':
        from . import skills
        from .config import load_settings
        url = body.get('url', '')
        proj = path or None

        def _install():
            exec_model = load_settings().get('omniroute_exec_model', '')
            ok, msg = skills.install_from_git(url, proj, exec_model)
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
        return {'ok': False, 'error': f'unknown job kind {kind!r}'}
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


# ── OmniRoute — free-tier execution backend ───────────────────
# github.com/diegosouzapw/OmniRoute (MIT, diegosouzapw). Self-hosted local
# proxy speaking the Anthropic Messages API natively — never returns the raw
# api_key to the frontend, status/models only.

def api_omniroute_status(q, body):
    from . import omniroute
    from .config import load_settings
    s = load_settings()
    base, key = s.get('omniroute_base_url', ''), s.get('omniroute_api_key', '')
    reachable = omniroute.is_reachable(base, key)
    out = {'reachable': reachable, 'model_count': 0, 'configured': 0, 'active': 0,
           'connections': []}
    if reachable:
        out['model_count'] = len(omniroute.list_models(base, key))
        out.update({k: v for k, v in omniroute.provider_status(base).items()
                    if k in ('configured', 'active')})
        out['connections'] = omniroute.cli_connections()
    return out


def api_omniroute_models(q, body):
    from . import omniroute
    from .config import load_settings
    s = load_settings()
    models = omniroute.list_models(s.get('omniroute_base_url', ''), s.get('omniroute_api_key', ''))
    return {'models': [m for m, _l in models], 'labels': {m: l for m, l in models}}


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
    '/api/hooks': api_hooks_get,
    '/api/agents/library': api_agents_library,
    '/api/agents/read': api_agent_read,
    '/api/agents/session': api_agents_session_get,
    '/api/skills': api_skills_get,
    '/api/skills/read': api_skill_read,
    '/api/worklog': api_worklog_get,
    '/api/mcp': api_mcp_get,
    '/api/accounts': api_accounts_get,
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
    '/api/omniroute/status': api_omniroute_status,
    '/api/omniroute/models': api_omniroute_models,
}

POST_ROUTES = {
    '/api/session/export': api_session_export,
    '/api/session/archive': api_session_archive,
    '/api/session/restore': api_session_restore,
    '/api/session/delete': api_session_delete,
    '/api/session/tags': api_tags_set,
    '/api/memory/autoscan': api_memory_autoscan,
    '/api/memory/auto': api_memory_auto_set,
    '/api/hooks/template': api_hooks_template,
    '/api/hooks/remove': api_hooks_remove,
    '/api/hooks/purge': api_hooks_purge,
    '/api/agents/create': api_agent_create,
    '/api/agents/delete': api_agent_delete,
    '/api/agents/session': api_agents_session,
    '/api/skills/install': api_skill_install,
    '/api/skills/remove': api_skill_remove,
    '/api/skills/create': api_skill_create,
    '/api/worklog': api_worklog_set,
    '/api/mcp/add': api_mcp_add,
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
    '/api/inject/launch': api_inject_launch,
    '/api/job': api_job_start,
    '/api/plan/edit': api_plan_edit,
}
