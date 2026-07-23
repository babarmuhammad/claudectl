"""Full-parity GUI API tests: endpoint groups + the job model with its
diff-approval gate, driven over real HTTP against a sandboxed server."""

import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, make_jsonl
from claude_sessions import gui, gui_api
from claude_sessions import config as config_mod


def _serve():
    srv = gui.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}'


def _req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers={'X-Claudectl': '1'},
                               method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def _seed(sb, monkeypatch, n=2, enc='X--enc-alpha'):
    actual = str(sb.root / 'work' / enc)
    os.makedirs(os.path.join(actual, 'node_modules'), exist_ok=True)
    folder = sb.projects / enc
    folder.mkdir()
    sids = []
    for i in range(n):
        sid = f'aaaa{i:04d}-0000-0000-0000-000000000000'
        make_jsonl(str(folder / f'{sid}.jsonl'), title=f'Session {i}')
        sids.append(sid)
    monkeypatch.setattr(gui, 'find_actual_path', lambda e: actual if e == enc else None)
    import claude_sessions.paths as paths_mod
    monkeypatch.setattr(paths_mod, 'find_actual_path',
                        lambda e, *a, **k: actual if e == enc else None)
    return actual, enc, str(folder), sids


def _c(actual, enc, sb):
    return {'path': actual, 'enc': enc, 'cfgdir': str(sb.cfg)}


# ── sessions & transcript ────────────────────────────────────

def test_transcript_meta_and_changed_files(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        q = f'enc={enc}&cfgdir={sb.cfg}&sid={sids[0]}'
        code, d = _req(f'{base}/api/transcript?{q}')
        assert code == 200 and len(d['messages']) >= 1
        assert any('hello world' in m['text'] for m in d['messages'])
        code, d = _req(f'{base}/api/session/meta?{q}')
        assert code == 200 and any('tokens' in l.lower() or 'in ' in l.lower()
                                   for l in d['lines'])
        code, d = _req(f'{base}/api/session/changed-files?{q}')
        assert code == 200 and 'files' in d
    finally:
        srv.shutdown()


def test_archive_restore_delete_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    body = {'enc': enc, 'cfgdir': str(sb.cfg), 'sid': sids[0]}
    try:
        code, d = _req(base + '/api/session/archive', body)
        assert d['ok'] and os.path.isfile(
            os.path.join(folder, 'archived', f'{sids[0]}.jsonl'))
        code, d = _req(f'{base}/api/session/archived?enc={enc}&cfgdir={sb.cfg}')
        assert [s['sid'] for s in d['sessions']] == [sids[0]]
        code, d = _req(base + '/api/session/restore', body)
        assert d['ok'] and os.path.isfile(os.path.join(folder, f'{sids[0]}.jsonl'))
        code, d = _req(base + '/api/session/delete', body)
        assert d['ok'] and not os.path.exists(os.path.join(folder, f'{sids[0]}.jsonl'))
    finally:
        srv.shutdown()


def test_tags_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        _req(base + '/api/session/tags', {'enc': enc, 'cfgdir': str(sb.cfg),
                                          'sid': sids[0], 'tags': ['wip', 'auth']})
        code, d = _req(f'{base}/api/session/tags?enc={enc}&cfgdir={sb.cfg}')
        assert d['tags'][sids[0]] == ['wip', 'auth']
    finally:
        srv.shutdown()


def test_export_writes_markdown(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/session/export',
                       {'enc': enc, 'cfgdir': str(sb.cfg), 'sid': sids[0],
                        'path': actual})
        assert d['ok'], d
    finally:
        srv.shutdown()


# ── usage & search ───────────────────────────────────────────

def test_usage_projects_and_daily_and_search(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/usage/projects')
        assert code == 200 and len(d['projects']) == 1
        p = d['projects'][0]
        assert p['enc'] == enc and p['sessions'] == 2 and p['usage']['in'] > 0
        code, d = _req(base + '/api/usage/project?enc=%s&cfgdir=%s' % (enc, sb.cfg))
        assert len(d['sessions']) == 2
        code, d = _req(base + '/api/search-index')
        assert code == 200 and len(d['rows']) == 2
        assert any('session 0' in r['haystack'] for r in d['rows'])
    finally:
        srv.shutdown()


# ── managers ─────────────────────────────────────────────────

def test_hooks_template_and_remove(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import hooks as hooks_mod
    hooks_file = tmp_path / 'hook-settings.json'
    monkeypatch.setattr(hooks_mod, 'settings_path', str(hooks_file))
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/hooks')
        assert code == 200 and d['templates']
        key = d['templates'][0]['key']
        code, d = _req(base + '/api/hooks/template', {'key': key})
        assert d['ok']
        code, d = _req(base + '/api/hooks')
        assert len(d['hooks']) >= 1
        h = d['hooks'][0]
        code, d = _req(base + '/api/hooks/remove',
                       {'event': h['event'], 'index': h['index']})
        assert d['ok']
    finally:
        srv.shutdown()


def test_agents_create_read_delete(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import agents as agents_mod
    udir = tmp_path / 'uagents'
    monkeypatch.setattr(agents_mod, 'user_agents_dir', lambda: str(udir))
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/agents/create',
                       {'name': 'test-bot', 'description': 'a test agent',
                        'scope': 'user', 'body': 'You are a test agent.'})
        assert d['ok'] and os.path.isfile(d['file'])
        code, r = _req(base + '/api/agents/read?file=' +
                       urllib.request.quote(d['file']))
        assert r['meta']['name'] == 'test-bot' and 'test agent' in r['body']
        code, r = _req(base + '/api/agents/delete', {'file': d['file']})
        assert r['ok'] and not os.path.exists(d['file'])
    finally:
        srv.shutdown()


def test_skills_endpoints(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(config_mod, 'skills_library_dir', str(tmp_path / 'sklib'))
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        # templates list includes the bundled starters
        code, d = _req(f'{base}/api/skills?path={urllib.request.quote(actual)}')
        assert code == 200
        names = [t['name'] for t in d['templates']]
        assert 'commit-message' in names and d['project'] == []

        # install a bundled template into the project
        tmpl = next(t for t in d['templates'] if t['name'] == 'commit-message')
        code, r = _req(base + '/api/skills/install', {'dir': tmpl['dir'], 'path': actual})
        assert r['ok'] and os.path.isfile(os.path.join(r['dir'], 'SKILL.md'))

        # now it shows under project skills
        code, d = _req(f'{base}/api/skills?path={urllib.request.quote(actual)}')
        assert any(s['name'] == 'commit-message' for s in d['project'])

        # read it back
        code, r = _req(base + '/api/skills/read?dir=' + urllib.request.quote(tmpl['dir']))
        assert r['meta']['name'] == 'commit-message'

        # create a fresh skill, then remove it
        code, r = _req(base + '/api/skills/create',
                       {'name': 'my new skill', 'description': 'does things',
                        'path': actual, 'body': '# X\n\nstep'})
        assert r['ok'] and os.path.isfile(os.path.join(r['dir'], 'SKILL.md'))
        code, r = _req(base + '/api/skills/remove', {'dir': r['dir']})
        assert r['ok']
    finally:
        srv.shutdown()


def test_worklog_endpoints(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(config_mod, 'settings_file', str(tmp_path / 'cl.json'))
    from claude_sessions import hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, 'settings_path', str(tmp_path / 'hooks.json'))
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    # seed a worklog entry on disk
    from claude_sessions import worklog
    worklog.add_entry(actual, {'session_id': 's1', 'ended_at': '2026-07-20T10:00:00Z',
                               'summary': 'did work', 'files': ['a.py']})
    srv, base = _serve()
    try:
        # off by default
        code, d = _req(f'{base}/api/worklog?path={urllib.request.quote(actual)}&enc={enc}')
        assert code == 200 and d['on'] is False
        assert d['entries'] and d['entries'][0]['summary'] == 'did work'
        # enable → persisted + hook installed
        code, r = _req(base + '/api/worklog', {'enc': enc, 'on': True})
        assert r['ok'] and r['on'] and r['installed']
        code, d = _req(f'{base}/api/worklog?path={urllib.request.quote(actual)}&enc={enc}')
        assert d['on'] is True
    finally:
        srv.shutdown()


def test_accounts_add_rename_remove(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(config_mod, '_USERPROFILE', str(tmp_path))
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/accounts/action',
                       {'action': 'add', 'name': 'work',
                        'dir': str(tmp_path / 'work-cfg')})
        assert d['ok']
        code, d = _req(base + '/api/accounts')
        assert any(a['name'] == 'work' for a in d['accounts'])
        code, d = _req(base + '/api/accounts/action',
                       {'action': 'rename', 'name': 'work', 'new': 'office'})
        assert d['ok']
        code, d = _req(base + '/api/accounts/action',
                       {'action': 'rename', 'name': 'office', 'new': 'default'})
        assert not d['ok']            # reserved name rejected
        code, d = _req(base + '/api/accounts/action',
                       {'action': 'remove', 'name': 'office'})
        assert d['ok']
        assert config_mod.load_settings()['accounts'] == []
    finally:
        srv.shutdown()


# ── memory suite ─────────────────────────────────────────────

def test_memory_state_lessons_audit_deny_workspace(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    # seed a lesson into the memory graph
    from claude_sessions import memory as memory_mod
    mem = memory_mod.load_memory(actual, folder)
    mem.setdefault('entities', []).append(
        {'id': 'l1', 'type': 'lesson', 'name': 'test lesson',
         'summary': 'always test', 'status': 'pending', 'kind': 'process',
         'confidence': 0.9})
    memory_mod.save_memory(actual, folder, mem)
    srv, base = _serve()
    c = f'path={urllib.request.quote(actual)}&enc={enc}&cfgdir={urllib.request.quote(str(sb.cfg))}'
    try:
        code, d = _req(f'{base}/api/memory/state?{c}')
        assert code == 200 and d['n_lessons'] == 1 and d['n_pending'] == 1
        code, d = _req(f'{base}/api/lessons?{c}')
        assert d['lessons'][0]['name'] == 'test lesson'
        code, d = _req(base + '/api/lessons',
                       {'path': actual, 'enc': enc, 'cfgdir': str(sb.cfg),
                        'id': 'l1', 'action': 'approve'})
        assert d['ok']
        code, d = _req(f'{base}/api/lessons?{c}')
        assert d['lessons'][0]['status'] == 'approved'
        code, d = _req(f'{base}/api/ctxaudit?{c}')
        assert code == 200 and 'items' in d and 'total' in d
        code, d = _req(f'{base}/api/deny?{c}')
        assert any('node_modules' in p['pattern'] for p in d['patterns'])
        code, d = _req(base + '/api/deny/apply', {'path': actual})
        assert d['ok'] and d['added'] >= 1
        proj_settings = json.load(open(os.path.join(actual, '.claude', 'settings.json'),
                                       encoding='utf-8'))
        assert any('node_modules' in x for x in proj_settings['permissions']['deny'])
        code, d = _req(f'{base}/api/workspace-status?{c}')
        assert code == 200 and 'lines' in d
    finally:
        srv.shutdown()


def _seed_graph_current(actual, folder):
    """A project with one source file and a memory graph whose provenance
    matches it → is_stale() False until the file is touched."""
    from claude_sessions import memory as memory_mod
    src = os.path.join(actual, 'app.py')
    with open(src, 'w', encoding='utf-8') as f:
        f.write('def hello():\n    return 1\n')
    # build provenance from the real units so hashes line up
    root = os.path.abspath(actual)
    units = memory_mod._units(actual, folder)
    from claude_sessions.workspace import _sha256_file
    prov, ents = {}, []
    for repo, module, fs in units:
        for fp in fs:
            prov[memory_mod._rel(root, fp)] = {'hash': _sha256_file(fp)}
        ents.append({'id': f'{repo}/{module}', 'type': 'component',
                     'name': module, 'summary': 's', 'repo': repo, 'module': module})
    memory_mod.save_memory(actual, folder, {'entities': ents, 'provenance': prov,
                                            'summaries': {}, 'relations': {},
                                            'generated_at': '2020-01-01T00:00:00'})
    return src


def test_autoscan_no_flash_when_current_then_runs_when_stale(monkeypatch, tmp_path):
    """The false-flash fix: opening an up-to-date project must NOT start a
    refresh (running:false, nothing invoked); a real file change makes it run."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    src = _seed_graph_current(actual, folder)
    from claude_sessions import gui_api
    calls = []
    monkeypatch.setattr(gui_api, '_refresh_async',
                        lambda p, f, **k: calls.append(p))
    sb.settings.write_text(json.dumps({'memory_auto_refresh': 'open'}),
                           encoding='utf-8')
    body = {'path': actual, 'enc': enc, 'cfgdir': str(sb.cfg)}
    srv, base = _serve()
    try:
        # current graph → no refresh, no flash
        code, d = _req(base + '/api/memory/autoscan', body)
        assert code == 200 and d['running'] is False and calls == []
        # touch the source → stale → refresh kicks off
        import time as _t
        with open(src, 'a', encoding='utf-8') as f:
            f.write('# changed\n')
        code, d = _req(base + '/api/memory/autoscan', body)
        assert d['running'] is True and calls == [actual]
    finally:
        srv.shutdown()


def test_is_stale_hash_check(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import memory as memory_mod
    # no graph yet → not stale (first build stays manual)
    assert memory_mod.is_stale(actual, folder) is False
    src = _seed_graph_current(actual, folder)
    assert memory_mod.is_stale(actual, folder) is False        # matches provenance
    with open(src, 'a', encoding='utf-8') as f:
        f.write('x = 2\n')
    assert memory_mod.is_stale(actual, folder) is True         # changed


def test_auto_memory_toggle_persists_and_lists(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch, enc='X--enc-toggle')
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/memory/auto', {'enc': enc, 'auto': True})
        assert d['ok']
        from claude_sessions import config as cfg
        assert cfg.load_settings()['project_defaults'][enc]['auto_memory'] is True
        code, d = _req(base + '/api/memory/auto')
        row = next(p for p in d['projects'] if p['enc'] == enc)
        assert row['auto'] is True and 'interval' in d
        # surfaced in list_projects payload
        from claude_sessions import gui
        assert any(p['encoded'] == enc and p['auto_memory']
                   for p in gui.list_projects())
        code, d = _req(base + '/api/memory/auto', {'interval': 1800})
        assert cfg.load_settings()['auto_memory_interval'] == 1800
    finally:
        srv.shutdown()


def test_auto_scan_pass_refreshes_only_enabled_and_stale(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.settings.write_text('{}', encoding='utf-8')   # clean baseline
    actual, enc, folder, sids = _seed(sb, monkeypatch, enc='X--enc-auto')
    src = _seed_graph_current(actual, folder)
    from claude_sessions import gui_api, memory as memory_mod, config as cfg
    refreshed = []
    monkeypatch.setattr(memory_mod, 'refresh_memory',
                        lambda p, f, n, **k: refreshed.append(p) or {'entities': []})

    # not opted in → skipped even though we make it stale
    with open(src, 'a', encoding='utf-8') as f:
        f.write('# a\n')
    gui_api._auto_scan_pass()
    assert refreshed == []

    # opt in but keep it current → skipped (nothing changed)
    s = cfg.load_settings()
    s.setdefault('project_defaults', {}).setdefault(enc, {})['auto_memory'] = True
    cfg.save_settings(s)
    _seed_graph_current(actual, folder)          # re-sync provenance to current
    gui_api._auto_scan_pass()
    assert refreshed == []

    # opted in AND stale → refreshed exactly once, lock cleared afterwards
    with open(src, 'a', encoding='utf-8') as f:
        f.write('# b\n')
    gui_api._auto_scan_pass()
    assert refreshed == [actual]
    assert memory_mod.scan_lock_status(actual) is None

    # a held lock (already running) → single-flight skip
    refreshed.clear()
    memory_mod.acquire_scan_lock(actual)
    try:
        with open(src, 'a', encoding='utf-8') as f:
            f.write('# c\n')
        gui_api._auto_scan_pass()
        assert refreshed == []
    finally:
        memory_mod.clear_scan_lock(actual)


def test_memory_active_reports_locked_project(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import memory as memory_mod
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/memory/active')
        assert actual not in d['active']
        memory_mod.acquire_scan_lock(actual)
        try:
            code, d = _req(base + '/api/memory/active')
            assert actual in d['active']
        finally:
            memory_mod.clear_scan_lock(actual)
    finally:
        srv.shutdown()


def test_claude_md_scaffold_and_system_prompt(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    c = {'path': actual, 'enc': enc, 'cfgdir': str(sb.cfg)}
    try:
        code, d = _req(base + '/api/claude-md/scaffold', c)
        assert d['ok'] and os.path.isfile(os.path.join(actual, 'CLAUDE.md'))
        code, d = _req(base + '/api/system-prompt',
                       {**c, 'text': 'Be terse.'})
        assert d['ok']
        code, d = _req(f'{base}/api/system-prompt?enc={enc}&cfgdir={sb.cfg}')
        assert d['text'] == 'Be terse.'
    finally:
        srv.shutdown()


# ── job model ────────────────────────────────────────────────

def test_job_gate_apply_and_reject(monkeypatch, tmp_path):
    """ai_compress via the job model: gate pauses the job, reject leaves the
    file untouched, apply writes it + .bak."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    md = os.path.join(actual, 'CLAUDE.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write('# alpha\n\nSome very long manual prose that goes on and on.\n')
    from claude_sessions import memory as memory_mod
    monkeypatch.setattr(memory_mod, '_claude_stdin',
                        lambda *a, **k: ('# alpha\n\n- compressed fact one that is '
                                         'definitely long enough to pass the eighty '
                                         'character minimum validation check easily\n'))
    srv, base = _serve()
    try:
        for decision, expect_written in ((False, False), (True, True)):
            code, d = _req(base + '/api/job',
                           {'kind': 'ai_compress', 'path': actual,
                            'enc': enc, 'cfgdir': str(sb.cfg)})
            assert d['ok'], d
            jid = d['job']
            st = None
            for _ in range(100):
                code, st = _req(f'{base}/api/job/{jid}')
                if st['status'] in ('awaiting', 'done', 'error'):
                    break
                time.sleep(0.05)
            assert st['status'] == 'awaiting', st
            assert 'COMPRESS' in st['gate']['title']
            assert any(l.startswith('+') for l in st['gate']['diff'])
            code, d = _req(f'{base}/api/job/{jid}/decide', {'apply': decision})
            assert d['ok']
            for _ in range(100):
                code, st = _req(f'{base}/api/job/{jid}')
                if st['status'] in ('done', 'error'):
                    break
                time.sleep(0.05)
            assert st['status'] == 'done', st
            text = open(md, encoding='utf-8').read()
            if expect_written:
                assert 'compressed fact one' in text
                assert os.path.isfile(md + '.bak')
            else:
                assert 'very long manual prose' in text
                assert not os.path.isfile(md + '.bak')
    finally:
        srv.shutdown()


def test_job_memory_ask(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import memory as memory_mod
    mem = memory_mod.load_memory(actual, folder)
    mem.setdefault('entities', []).append(
        {'id': 'e1', 'type': 'component', 'name': 'main.py',
         'summary': 'entrypoint'})
    memory_mod.save_memory(actual, folder, mem)
    monkeypatch.setattr(memory_mod, '_claude_stdin',
                        lambda *a, **k: 'main.py is the entrypoint.')
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'memory_ask', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'question': 'what is main.py?'})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert 'entrypoint' in str(st.get('result', ''))
    finally:
        srv.shutdown()


def test_job_review(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import review as review_mod, memory as memory_mod
    monkeypatch.setattr(review_mod, 'get_diff', lambda *a, **k: 'diff\n+bad = eval(x)')
    monkeypatch.setattr(review_mod, 'gather_guidance', lambda *a, **k: '')
    monkeypatch.setattr(memory_mod, '_claude_stdin', lambda *a, **k:
                        '[{"file":"f.py","line":9,"confidence":95,'
                        '"severity":"critical","category":"security","summary":"eval"}]')
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'review', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg)})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        res = st['result']
        assert res['findings'][0]['file'] == 'f.py'
        assert res['findings'][0]['severity'] == 'critical'
    finally:
        srv.shutdown()


def test_job_plan_make_with_council(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import plan_execute
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'draft plan')
    seen = {}
    def fake_council(task, plan, cwd, models=None, omni_env=None):
        seen['called'] = (task, plan)
        return 'council-optimized plan'
    monkeypatch.setattr(plan_execute, 'optimize_plan_council', fake_council)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'plan_make', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'task': 'do a thing', 'council': True})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert seen['called'] == ('do a thing', 'draft plan')
        assert st['result']['plan'] == 'council-optimized plan'
    finally:
        srv.shutdown()


def test_job_plan_make_without_council_skips_optimizer(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import plan_execute
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'draft plan')
    def boom(*a, **k):
        raise AssertionError('council must not run when disabled')
    monkeypatch.setattr(plan_execute, 'optimize_plan_council', boom)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'plan_make', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'task': 'do a thing'})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert st['result']['plan'] == 'draft plan'
    finally:
        srv.shutdown()


def test_job_plan_make_council_ignores_stale_omniroute_default(monkeypatch, tmp_path):
    # regression: council used to always route through the account-wide
    # omniroute_exec_model default regardless of the 'via' the user picked
    # for THIS plan -- a stale/unreachable default silently killed every
    # council call (via _headless's own failure swallowing) with no error
    # surfaced. Council must follow the request's own 'via', same as
    # plan_launch already does.
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import plan_execute, config as cfg
    s = cfg.load_settings()
    s['omniroute_exec_model'] = 'auto/coding'
    s['omniroute_base_url'] = 'http://127.0.0.1:1'
    cfg.save_settings(s)
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'draft plan')
    seen = {}
    def fake_council(task, plan, cwd, models=None, omni_env=None):
        seen['omni_env'] = omni_env
        return 'council-optimized plan'
    monkeypatch.setattr(plan_execute, 'optimize_plan_council', fake_council)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'plan_make', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'task': 'do a thing', 'council': True,
                        'via': 'anthropic'})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert seen['omni_env'] == {}
    finally:
        srv.shutdown()


def test_job_plan_make_council_uses_omniroute_when_via_selected(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import plan_execute, config as cfg
    s = cfg.load_settings()
    s['omniroute_exec_model'] = 'auto/coding'
    s['omniroute_base_url'] = 'http://localhost:20128'
    s['omniroute_api_key'] = 'secret'
    cfg.save_settings(s)
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'draft plan')
    seen = {}
    def fake_council(task, plan, cwd, models=None, omni_env=None):
        seen['omni_env'] = omni_env
        return 'council-optimized plan'
    monkeypatch.setattr(plan_execute, 'optimize_plan_council', fake_council)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'plan_make', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'task': 'do a thing', 'council': True,
                        'via': 'omniroute'})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert seen['omni_env'] == {'ANTHROPIC_BASE_URL': 'http://localhost:20128',
                                     'ANTHROPIC_AUTH_TOKEN': 'secret'}
    finally:
        srv.shutdown()


def test_job_plan_launch_forwards_account(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import plan_execute, config as config_mod2
    monkeypatch.setattr(config_mod2, 'get_claude_exe', lambda: r'C:\fake.exe')
    other_acct = str(tmp_path / 'other-account')
    captured = {}
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda args, **k: captured.setdefault('env', k.get('env')) or None)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job',
                       {'kind': 'plan_launch', 'path': actual, 'enc': enc,
                        'cfgdir': str(sb.cfg), 'task': 'do a thing', 'account': other_acct})
        jid = d['job']
        for _ in range(100):
            code, st = _req(f'{base}/api/job/{jid}')
            if st['status'] in ('done', 'error'):
                break
            time.sleep(0.05)
        assert st['status'] == 'done'
        assert captured['env']['CLAUDE_CONFIG_DIR'] == other_acct
    finally:
        srv.shutdown()


def test_unknown_job_kind_rejected(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/job', {'kind': 'rm_rf_everything'})
        assert not d['ok'] and 'unknown' in d['error']
    finally:
        srv.shutdown()


def test_bridge_inert_outside_jobs(monkeypatch, tmp_path, capsys):
    """Outside a job thread the patched flash falls through to the original
    TUI implementation, which prints to the console (TUI regression guard)."""
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import ui as ui_mod
    ui_mod.flash('hello tui')
    assert 'hello tui' in capsys.readouterr().out


# ── new parity endpoints: extra paths / add dirs / themes / progress ──

def test_extra_paths_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/extra-paths',
                       {**_c(actual, enc, sb), 'paths': [r'C:\tools\bin', '', '  ']})
        assert d['ok']
        code, d = _req(f'{base}/api/extra-paths?enc={enc}&cfgdir={sb.cfg}')
        assert code == 200 and d['paths'] == [r'C:\tools\bin']
    finally:
        srv.shutdown()


def test_add_dirs_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/add-dirs',
                       {**_c(actual, enc, sb), 'dirs': [str(sb.root), '']})
        assert d['ok']
        code, d = _req(f'{base}/api/add-dirs?enc={enc}&cfgdir={sb.cfg}')
        assert code == 200 and d['dirs'] == [str(sb.root)]
    finally:
        srv.shutdown()


def test_state_exposes_theme_palettes(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/state')
        assert code == 200
        themes = d['themes']
        assert set(config_mod.THEMES) <= set(themes)
        for pal in themes.values():
            for key in ('accent', 'accent2', 'ok', 'warn',
                        'bg', 'bg2', 'panel', 'panel2', 'line',
                        'txt', 'dim', 'dim2', 'code'):
                assert pal[key].startswith('#') and len(pal[key]) == 7
    finally:
        srv.shutdown()


def test_memory_progress_endpoint(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    srv, base = _serve()
    try:
        code, d = _req(f'{base}/api/memory/progress?path={actual}')
        assert code == 200 and d['progress'] is None
        from claude_sessions import memory as memory_mod
        assert memory_mod.acquire_scan_lock(actual)
        memory_mod._report_progress('3/9 modules')
        code, d = _req(f'{base}/api/memory/progress?path={actual}')
        assert code == 200 and '3/9' in d['progress']
        memory_mod.clear_scan_lock(actual)
    finally:
        srv.shutdown()


def test_gui_page_has_no_emoji_and_has_icons(monkeypatch, tmp_path):
    from claude_sessions.gui_html import PAGE
    assert 'const ICONS' in PAGE and 'applyTheme' in PAGE
    banned = [chr(c) for c in range(0x1F300, 0x1FAFF)]
    assert not any(ch in PAGE for ch in banned)


def test_open_project_by_path(monkeypatch, tmp_path):
    """Open-project-by-path mirrors the TUI __open_path__ branch: reject a
    non-directory, and for a real folder return the same encoded name the TUI
    computes, plus folder auto-completion."""
    from claude_sessions.paths import encode_component
    sb = Sandbox(monkeypatch, tmp_path)
    proj = tmp_path / 'work' / 'newproj'
    (proj / 'child').mkdir(parents=True)
    srv, base = _serve()
    try:
        # bogus path is rejected (never launches)
        code, d = _req(base + '/api/open-path', {'path': str(tmp_path / 'nope')})
        assert code == 200 and d['ok'] is False and d['error']
        # real folder → same enc the TUI's encode_component produces
        code, d = _req(base + '/api/open-path', {'path': str(proj)})
        assert code == 200 and d['ok']
        assert d['enc'] == encode_component(os.path.abspath(str(proj)))
        assert d['name'] == 'newproj'
        # completion surfaces the child directory
        code, c = _req(f'{base}/api/path-complete?text={proj}' + os.sep)
        assert code == 200
        assert any(os.path.basename(p) == 'child' for p in c['dirs'])
    finally:
        srv.shutdown()


def test_gui_page_has_open_project_wiring(monkeypatch, tmp_path):
    from claude_sessions.gui_html import PAGE
    assert 'openProjectByPath' in PAGE and 'bOpenPath' in PAGE
    assert "/api/open-path" in PAGE and "/api/path-complete" in PAGE


def test_gui_launch_modal_redesign(monkeypatch, tmp_path):
    """Part B launch-modal redesign: real effort slider (not a stray chips
    div), Advanced fields collapsed behind a <details>, design tokens in
    :root, and the Resume button rendered outside the hover-only .acts group."""
    from claude_sessions.gui_html import PAGE
    assert '<input type="range" id="fEffort"' in PAGE
    assert 'chipVal($(\'#fEffort\'))' not in PAGE   # old dead-code bug
    assert '<details class="adv">' in PAGE
    assert '--r-sm:' in PAGE and '--sp1:' in PAGE and '--mono:' in PAGE
    # primary Resume/Restore button sits after the hover-revealed .acts block
    sess_tpl = PAGE[PAGE.index('function drawSessions'):PAGE.index('function resumeS')]
    assert 'class="acts">' in sess_tpl
    acts_close = sess_tpl.index('</div>', sess_tpl.index('class="acts">'))
    pri_idx = sess_tpl.index('btn sm pri')
    assert pri_idx > acts_close


def test_list_projects_exposes_last_active(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import gui
    rows = gui.list_projects()
    assert rows and all('last_active' in r and r['last_active'] for r in rows)


def test_gui_launch_modal_frontier_slider(monkeypatch, tmp_path):
    """The model+effort picker is now a single frontier slider by default;
    the old raw model-card grid + effort slider still exist but only as a
    'pin an exact model + effort' override tucked inside Advanced."""
    from claude_sessions.gui_html import PAGE
    assert '<input type="range" id="fFrontier"' in PAGE
    assert 'id="fPinModel"' in PAGE and 'id="fPinBlock"' in PAGE
    assert 'function currentModelEffort' in PAGE and 'function frontierRow' in PAGE
    assert 'function setPinMode' in PAGE
    # doLaunch must resolve through currentModelEffort(), not a stale chip/card read
    do_launch = PAGE[PAGE.index('async function doLaunch'):]
    assert 'currentModelEffort()' in do_launch[:400]
    # the raw pin controls sit inside the Advanced <details>, not top-level
    adv = PAGE[PAGE.index('<details class="adv">'):PAGE.index('</details>')]
    assert 'id="fModel"' in adv and 'id="fEffort"' in adv
    modal_body = PAGE[PAGE.index('<h3 id="mTitle">'):PAGE.index('<details class="adv">')]
    assert 'id="fModel"' not in modal_body and 'id="fEffort"' not in modal_body


def test_gui_state_exposes_frontier(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/state')
        assert code == 200
        frontier = d['options']['frontier']
        assert len(frontier) == 7
        assert frontier[0][0] == 'claude-haiku-4-5'
        assert frontier[-1][0] == 'claude-fable-5'
    finally:
        srv.shutdown()


def test_gui_home_bento_dashboard_and_recent_age(monkeypatch, tmp_path):
    """Home is a bento-style dashboard (continue/usage/projects/search/actions
    tiles), not the old 3D swipeable card deck -- and each recent row still
    carries a human 'age' for the continue tile."""
    from claude_sessions.gui_html import PAGE
    # the old deck is gone entirely
    for dead in ('deckMarkup', 'renderDeck', 'bindDeckSwipe', 'deckNext',
                 'deckPrev', 'deckBring', 'deckCardClick', 'DECK_ORDER'):
        assert dead not in PAGE, dead
    assert 'class="deck"' not in PAGE and 'class="deckcard"' not in PAGE
    # the new dashboard tiles exist
    assert 'class="bento"' in PAGE
    for tile in ('t-continue', 't-usage', 't-projects', 't-search', 't-actions'):
        assert f'class="card {tile}"' in PAGE
    assert 'function continueTileHtml' in PAGE and 'function projectsTileHtml' in PAGE
    assert 'function loadHomeUsage' in PAGE and 'function drawHomeSearchResults' in PAGE
    # continue-tile resume reuses the one-click + inline-tune helpers, not askLaunch
    home_resume = PAGE[PAGE.index('function homeResume('):PAGE.index('function toggleHomeTune')]
    assert 'askLaunch' not in home_resume and 'doQuickLaunch' in home_resume
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = _seed(sb, monkeypatch)
    from claude_sessions import sessions as sessions_mod
    monkeypatch.setattr(sessions_mod, 'load_recent_sessions', lambda n=5: [
        {'project_path': actual, 'encoded_name': enc, 'session_id': sids[0],
         'timestamp': time.time(), 'cfgdir': str(sb.cfg)}])
    from claude_sessions import gui
    payload = gui.state_payload()
    assert payload['recent'] and payload['recent'][0]['age']


def test_gui_project_resume_is_one_click_with_inline_tune(monkeypatch, tmp_path):
    """Clicking Resume on a session inside a project must NOT open the
    launch modal (askLaunch) — it launches immediately via doQuickLaunch
    with the recommended/last-used model+effort. A secondary settings icon
    expands that row in place (.rowtune) to override power before resuming,
    with no overlay/dimming involved."""
    from claude_sessions.gui_html import PAGE
    resume_s = PAGE[PAGE.index('function resumeS('):PAGE.index('function forkS')]
    assert 'askLaunch' not in resume_s
    assert 'doQuickLaunch' in resume_s
    assert 'function defaultModelEffort' in PAGE
    assert 'function toggleTune' in PAGE and 'function resumeTuned' in PAGE
    assert 'class="rowtune"' in PAGE
    # Fork/New-session/Continue still use the full modal (more fields, less frequent)
    assert 'askLaunch' in PAGE[PAGE.index('function forkS'):PAGE.index('function forkS')+200]


def test_hook_template_installed_flag(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    from claude_sessions import hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, 'settings_path', str(tmp_path / 'hook-settings.json'))
    srv, base = _serve()
    try:
        code, d = _req(base + '/api/hooks')
        key = d['templates'][0]['key']
        assert not any(t['installed'] for t in d['templates'])
        code, d = _req(base + '/api/hooks/template', {'key': key})
        assert d['ok']
        code, d = _req(base + '/api/hooks')
        flags = {t['key']: t['installed'] for t in d['templates']}
        assert flags[key] is True
    finally:
        srv.shutdown()


def test_plan_edit_endpoint(monkeypatch, tmp_path):
    """Edit-plan via GUI API returns OK + modified plan for valid edits,
    and returns error + no plan for invalid operations."""
    from claude_sessions import plan_execute
    srv, base = _serve()
    plan = '1. first step\n2. second step\n3. third step'
    try:
        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'edit', 'index': 1, 'text': 'modified'})
        assert d['ok'] and 'modified' in d['plan'] and 'first step' in d['plan']

        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'delete', 'index': 0})
        assert d['ok'] and 'second step' in d['plan'] and 'first step' not in d['plan']

        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'insert', 'index': 1, 'text': 'new'})
        assert d['ok'] and d['plan'].count('. ') == 4

        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'move', 'index': 0, 'text': '2'})
        assert d['ok']

        # invalid index returns error
        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'edit', 'index': 99, 'text': 'x'})
        assert not d['ok'] and 'error' in d

        # empty text returns error
        code, d = _req(base + '/api/plan/edit',
                       {'plan': plan, 'action': 'edit', 'index': 0, 'text': ''})
        assert not d['ok'] and 'error' in d

        # no-number plan returns error
        code, d = _req(base + '/api/plan/edit',
                       {'plan': 'just text', 'action': 'edit', 'index': 0, 'text': 'x'})
        assert not d['ok'] and 'error' in d
    finally:
        srv.shutdown()
