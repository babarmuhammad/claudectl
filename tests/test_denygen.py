"""F4a — deny-rules generator."""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, ENTER, ESC, RIGHT, run_flow

from claude_sessions import denygen


def _seed(tmp_path):
    proj = tmp_path / 'proj'
    (proj / 'node_modules' / 'lib').mkdir(parents=True)
    (proj / 'node_modules' / 'lib' / 'x.js').write_text('x')
    (proj / 'sub' / '__pycache__').mkdir(parents=True)
    (proj / 'sub' / '__pycache__' / 'y.pyc').write_text('y')
    (proj / 'yarn.lock').write_text('lock ' * 100)
    (proj / '.git').mkdir()
    (proj / 'src').mkdir()
    (proj / 'src' / 'app.py').write_text('code')
    return str(proj)


def test_scan_heavy_finds_only_present(tmp_path):
    proj = _seed(tmp_path)
    pats = dict(denygen.scan_heavy(proj))
    assert 'Read(node_modules/**)' in pats
    assert 'Read(sub/__pycache__/**)' in pats
    assert 'Read(**/yarn.lock)' in pats
    assert not any('dist' in p or 'package-lock' in p for p in pats)
    assert not any('.git' in p for p in pats)


def test_merge_deny_preserves_and_idempotent(tmp_path):
    proj = _seed(tmp_path)
    sp = os.path.join(proj, '.claude', 'settings.json')
    os.makedirs(os.path.dirname(sp))
    with open(sp, 'w', encoding='utf-8') as f:
        json.dump({'permissions': {'deny': ['Read(node_modules/**)'],
                                   'allow': ['Bash(git:*)']},
                   'hooks': {'Stop': [{'hooks': []}]}}, f)
    pats = [p for p, _ in denygen.scan_heavy(proj)]
    added, existed = denygen.merge_deny(proj, pats)
    assert added == 2 and existed == 1
    d = json.load(open(sp, encoding='utf-8'))
    assert d['permissions']['allow'] == ['Bash(git:*)']      # untouched
    assert 'Stop' in d['hooks']                              # untouched
    assert sorted(d['permissions']['deny']) == sorted(set(pats))
    added2, existed2 = denygen.merge_deny(proj, pats)        # idempotent
    assert (added2, existed2) == (0, 3)


def test_deny_rules_screen_writes_on_confirm(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    proj = _seed(tmp_path)
    # ENTER (write) → confirm Yes (RIGHT, ENTER)
    _ret, cap, _ex = run_flow(monkeypatch, [*ENTER, *RIGHT, *ENTER],
                              denygen.deny_rules_screen, proj, 'proj')
    assert 'DENY RULES' in cap.plain
    sp = os.path.join(proj, '.claude', 'settings.json')
    d = json.load(open(sp, encoding='utf-8'))
    assert 'Read(node_modules/**)' in d['permissions']['deny']


def test_deny_rules_screen_esc_writes_nothing(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    proj = _seed(tmp_path)
    run_flow(monkeypatch, [*ESC], denygen.deny_rules_screen, proj, 'proj')
    assert not os.path.exists(os.path.join(proj, '.claude', 'settings.json'))
