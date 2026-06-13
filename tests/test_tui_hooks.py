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
    assert d['hooks']['PostToolUse'][0]['matcher'] == 'Edit|Write'


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
