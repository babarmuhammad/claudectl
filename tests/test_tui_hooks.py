import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, RIGHT, ENTER, ESC

from claude_sessions import hooks


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def _point_settings(monkeypatch, tmp_path):
    sp = str(tmp_path / 'settings.json')
    monkeypatch.setattr(hooks, 'settings_path', sp)
    return sp


def test_add_template(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    # selectables on empty: Add from template, Edit settings.json
    keys = flat(ENTER,        # Add from template
                ENTER,        # first template (prettier-on-edit)
                ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8'))
    assert 'PostToolUse' in d['hooks']
    assert d['hooks']['PostToolUse'][0]['matcher'].startswith('Edit|Write')


def test_toggle_disables_hook(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'beep'}]}]}},
              open(sp, 'w', encoding='utf-8'))
    # first row = the Stop hook; ENTER -> action menu -> Toggle (first)
    keys = flat(ENTER, ENTER, ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8'))
    assert 'Stop' not in d.get('hooks', {})
    assert 'Stop' in d.get('hooks_disabled', {})


def test_remove_hook(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    json.dump({'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'beep'}]}]}},
              open(sp, 'w', encoding='utf-8'))
    # row ENTER -> action menu DOWN to Remove -> ENTER -> confirm No->Yes
    keys = flat(ENTER, DOWN, ENTER, RIGHT, ENTER, ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8'))
    assert not d.get('hooks', {}).get('Stop')


def test_empty_renders(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _point_settings(monkeypatch, tmp_path)
    _, cap, _ = run_flow(monkeypatch, flat(ESC), hooks.hooks_menu)
    assert 'HOOKS' in cap.plain
    assert 'no hooks configured' in cap.plain


def test_corrupt_settings_tolerated(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    open(sp, 'w', encoding='utf-8').write('{{{bad json')
    _, cap, _ = run_flow(monkeypatch, flat(ESC), hooks.hooks_menu)
    assert 'HOOKS' in cap.plain   # no crash


def test_all_templates_well_formed():
    assert len(hooks.TEMPLATES) >= 15
    for name, tpl in hooks.TEMPLATES.items():
        assert tpl['event'] in hooks.EVENTS, name
        hs = tpl['entry']['hooks']
        assert hs and all(h['type'] == 'command' and h['command'] for h in hs), name
        # blocks/log must NOT use PowerShell $-parsing (breaks under bash hooks)
        for h in hs:
            assert 'ConvertFrom-Json' not in h['command'], name


def test_guard_hook_blocks_and_passes(tmp_path):
    import subprocess
    guard = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'claude_sessions', 'guard_hook.py')
    def run(payload, *args):
        return subprocess.run([sys.executable, guard, *args], input=payload,
                              capture_output=True, text=True, timeout=15).returncode
    assert run('{"tool_input":{"command":"rm -rf /"}}', 'command', r'rm\s+-rf', 'x') == 2
    assert run('{"tool_input":{"command":"ls -la"}}', 'command', r'rm\s+-rf', 'x') == 0
    assert run('{"tool_input":{"file_path":"a/.env"}}', 'file_path', r'\.env', 'x') == 2
    assert run('not json', 'command', 'x', 'y') == 0            # never wrongly block


def test_is_broken_detects_legacy():
    assert hooks._is_broken('powershell -c "$j=$input|ConvertFrom-Json; ..."')
    assert hooks._is_broken('prettier --write .')            # unguarded formatter
    assert not hooks._is_broken('command -v prettier >/dev/null 2>&1 && prettier --write . || true')
    assert not hooks._is_broken('git status -sb')
    assert not hooks._is_broken('"C:\\py.exe" "guard_hook.py" command "rm" "x"')


def test_purge_removes_broken_hooks(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    json.dump({'hooks': {
        'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command',
                        'command': 'powershell -c "$j=$input|ConvertFrom-Json"'}]}],
        'PostToolUse': [{'matcher': 'Edit', 'hooks': [{'type': 'command', 'command': 'prettier --write .'}]}],
        'Stop': [{'hooks': [{'type': 'command', 'command': 'git status -sb'}]}]}},
        open(sp, 'w', encoding='utf-8'))
    # menu: 3rd action after the 3 hook rows -> nav to "Remove broken", confirm Yes
    # rows: 3 hooks, sep, Add, AI, Purge, Edit  -> Purge is 6th selectable (idx 5)
    keys = flat(DOWN, DOWN, DOWN, DOWN, DOWN, ENTER, RIGHT, ENTER, ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8'))
    assert 'PreToolUse' not in d.get('hooks', {})            # legacy powershell gone
    assert 'PostToolUse' not in d.get('hooks', {})           # unguarded prettier gone
    assert 'Stop' in d['hooks']                              # git status kept


def test_logbash_hook_appends(tmp_path):
    import subprocess
    lb = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'claude_sessions', 'logbash_hook.py')
    payload = json.dumps({'cwd': str(tmp_path), 'tool_input': {'command': 'git status'}})
    subprocess.run([sys.executable, lb], input=payload, capture_output=True,
                   text=True, timeout=15)
    log = tmp_path / '.claudectl' / 'bash-log.txt'
    assert log.is_file() and 'git status' in log.read_text(encoding='utf-8')


def test_ai_hook_generates_and_saves(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    from claude_sessions import memory
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: json.dumps({
        'event': 'PostToolUse', 'matcher': 'Edit|Write',
        'command': 'echo done', 'desc': 'demo'}))
    # AI-generate is the 2nd action on empty menu (Add template, AI-generate, Edit)
    # type description, ENTER; confirm Add (ENTER)
    keys = flat(DOWN, ENTER, typed('beep after edits'), ENTER, RIGHT, ENTER, ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8'))
    entry = d['hooks']['PostToolUse'][0]
    assert entry['matcher'] == 'Edit|Write'
    assert entry['hooks'][0]['command'] == 'echo done'


def test_ai_hook_rejects_invalid_event(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sp = _point_settings(monkeypatch, tmp_path)
    from claude_sessions import memory
    monkeypatch.setattr(memory, '_claude_stdin', lambda *a, **k: json.dumps({
        'event': 'Nonsense', 'command': 'x'}))
    keys = flat(DOWN, ENTER, typed('bad'), ENTER, ESC)
    run_flow(monkeypatch, keys, hooks.hooks_menu)
    d = json.load(open(sp, encoding='utf-8')) if os.path.isfile(sp) else {}
    assert not d.get('hooks')                       # nothing saved
