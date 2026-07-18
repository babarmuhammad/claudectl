"""GUI server tests — real HTTP requests against a sandboxed server on an
ephemeral port, plus launch parity with the TUI's build_launch_command."""

import json
import os
import subprocess
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, make_jsonl
from claude_sessions import gui
from claude_sessions import main as main_mod
from claude_sessions import config as config_mod


def _serve(monkeypatch):
    srv = gui.make_server(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}'


def _req(url, body=None, headers=None):
    h = {'X-Claudectl': '1'}
    if headers is not None:
        h = headers
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=h,
                               method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or b'{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def _seed(sb, monkeypatch):
    """One project with one titled session, resolvable by gui.list_projects."""
    actual = str(sb.root / 'work' / 'alpha')
    os.makedirs(actual, exist_ok=True)
    enc = 'X--enc-alpha'
    folder = sb.projects / enc
    folder.mkdir()
    sid = 'aaaa0000-0000-0000-0000-000000000000'
    make_jsonl(str(folder / f'{sid}.jsonl'), title='Fix the bug')
    monkeypatch.setattr(gui, 'find_actual_path', lambda e: actual if e == enc else None)
    return actual, enc, sid


def test_list_projects_and_sessions(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, sid = _seed(sb, monkeypatch)
    projs = gui.list_projects()
    assert len(projs) == 1
    assert projs[0]['encoded'] == enc and projs[0]['path'] == actual
    assert projs[0]['accounts'] == ['default']
    sess = gui.list_sessions(enc)
    assert len(sess) == 1
    assert sess[0]['sid'] == sid and sess[0]['title'] == 'Fix the bug'
    assert sess[0]['account'] == 'default'


def test_http_state_and_sessions(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, sid = _seed(sb, monkeypatch)
    srv, base = _serve(monkeypatch)
    try:
        code, st = _req(base + '/api/state')
        assert code == 200
        assert st['projects'][0]['encoded'] == enc
        assert st['ui_mode'] == 'tui'
        assert 'efforts' in st['options'] and 'models' in st['options']
        code, d = _req(base + f'/api/sessions?enc={enc}')
        assert code == 200 and d['sessions'][0]['sid'] == sid
    finally:
        srv.shutdown()


def test_http_guard_rejects_missing_header(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve(monkeypatch)
    try:
        code, d = _req(base + '/api/state', headers={})   # no X-Claudectl
        assert code == 403
        code, d = _req(base + '/api/launch', body={'path': 'x'}, headers={})
        assert code == 403
    finally:
        srv.shutdown()


def test_http_launch_spawns_new_console(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, sid = _seed(sb, monkeypatch)
    calls = []
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda cmd, **kw: calls.append((cmd, kw)) or None)
    srv, base = _serve(monkeypatch)
    try:
        code, d = _req(base + '/api/launch', body={
            'path': actual, 'enc': enc, 'choice': f'resume:{sid}',
            'opts': {'effort': 'high', 'model': 'claude-sonnet-5'}})
        assert code == 200 and d['ok'], d
    finally:
        srv.shutdown()
    (cmd, kw), = [c for c in calls if c[0][:2] == ['cmd', '/c']]
    # new-console pattern: cmd /c title "<title with space>" && <claude args>
    # (no `|| pause` — window must always close when claude exits, any code)
    assert cmd[2] == 'title'
    assert ' ' in cmd[3]                     # title must be quoted by list2cmdline
    assert cmd[4] == '&&'
    claude_args = cmd[5:]
    assert ['-r', sid] == claude_args[1:3]
    assert '--effort' in claude_args and 'high' in claude_args
    assert '--model' in claude_args and 'claude-sonnet-5' in claude_args
    assert kw['cwd'] == actual
    assert kw['env']['CLAUDE_CONFIG_DIR'] == str(sb.cfg)
    assert kw['creationflags'] == subprocess.CREATE_NEW_CONSOLE


def test_launch_parity_with_tui_builder(monkeypatch, tmp_path):
    """The argv the GUI hands to the new console must be exactly what the
    TUI's build_launch_command produces for the same inputs."""
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, sid = _seed(sb, monkeypatch)
    opts = {'effort': 'low', 'model': '', 'perm': 'plan', 'name': '',
            'worktree': '', 'agent': '', 'agents_json': '', 'cfgdir': '',
            'max_thinking': '8000', 'subagent_model': 'claude-haiku-4-5'}
    args, env, _pf = main_mod.build_launch_command(actual, enc, f'fork:{sid}', opts)
    calls = []
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda cmd, **kw: calls.append((cmd, kw)) or None)
    ok, err = gui.launch_session(actual, enc, f'fork:{sid}', opts)
    assert ok, err
    (cmd, kw), = [c for c in calls if c[0][:2] == ['cmd', '/c']]
    assert cmd[5:] == args                      # exact argv parity, no trailing `|| pause`
    assert kw['env']['MAX_THINKING_TOKENS'] == '8000'
    assert kw['env']['CLAUDE_CODE_SUBAGENT_MODEL'] == 'claude-haiku-4-5'
    assert env['CLAUDE_CONFIG_DIR'] == kw['env']['CLAUDE_CONFIG_DIR']


def test_settings_ui_mode_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve(monkeypatch)
    try:
        code, d = _req(base + '/api/settings', body={'ui_mode': 'gui'})
        assert code == 200 and d['ok']
    finally:
        srv.shutdown()
    assert config_mod.load_settings()['ui_mode'] == 'gui'
    # invalid values are ignored, not saved
    srv, base = _serve(monkeypatch)
    try:
        _req(base + '/api/settings', body={'ui_mode': 'evil'})
    finally:
        srv.shutdown()
    assert config_mod.load_settings()['ui_mode'] == 'gui'


def test_rename_via_api(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, sid = _seed(sb, monkeypatch)
    srv, base = _serve(monkeypatch)
    try:
        code, d = _req(base + '/api/rename', body={
            'enc': enc, 'cfgdir': str(sb.cfg), 'sid': sid, 'name': 'My Feature'})
        assert code == 200 and d['ok']
    finally:
        srv.shutdown()
    from claude_sessions.sessions import load_name
    assert load_name(str(sb.projects / enc), sid) == 'My Feature'


def test_index_serves_html(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    srv, base = _serve(monkeypatch)
    try:
        with urllib.request.urlopen(base + '/') as r:
            body = r.read().decode('utf-8')
            assert r.status == 200
            assert 'claudectl' in body and '<html' in body
    finally:
        srv.shutdown()
