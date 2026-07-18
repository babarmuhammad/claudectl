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


def _seed(sb, monkeypatch, n=2):
    actual = str(sb.root / 'work' / 'alpha')
    os.makedirs(os.path.join(actual, 'node_modules'), exist_ok=True)
    enc = 'X--enc-alpha'
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
