import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox

from claude_sessions import brief, memory


def test_work_suggestions_from_signals(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    mem = memory._empty()
    mem['entities'] = [
        {'name': 'Fix1', 'type': 'lesson', 'kind': 'error_fix', 'status': 'approved',
         'summary': 'retry-after backoff needed', 'repo': '', 'module': ''},
        {'name': 'Engine', 'type': 'component', 'summary': 'core', 'repo': 'app',
         'module': 'engine', 'rank': 20},
    ]
    memory.save_memory(actual, folder, mem)
    sug = brief.work_suggestions(actual, folder)
    txts = ' '.join(t for _tag, t in sug)
    assert 'recurring issue' in txts and 'retry-after' in txts
    assert 'central module' in txts and 'app/engine' in txts


def test_work_suggestions_empty(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    sug = brief.work_suggestions(actual, folder)
    assert sug and ('no signals' in sug[0][1] or 'no semantic memory' in ' '.join(t for _s, t in sug))


def test_session_diff_non_git(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    out = brief.session_diff(actual, folder)
    assert out and 'nothing to diff' in out[0]


def test_session_diff_git(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    def _g(*a):
        subprocess.run(['git', *a], cwd=actual, capture_output=True, text=True)
    _g('init'); _g('config', 'user.email', 't@t'); _g('config', 'user.name', 't')
    open(os.path.join(actual, 'f.txt'), 'w').write('x')
    _g('add', '-A'); _g('commit', '-m', 'first commit here')
    out = brief.session_diff(actual, folder)
    assert any('commit' in l.lower() for l in out)


def test_session_diff_subproject_repo(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    # NO git at root — repo lives in a sub-project
    sub = os.path.join(actual, 'service')
    os.makedirs(sub)
    def _g(*a):
        subprocess.run(['git', *a], cwd=sub, capture_output=True, text=True)
    _g('init'); _g('config', 'user.email', 't@t'); _g('config', 'user.name', 't')
    open(os.path.join(sub, 'f.txt'), 'w').write('x')
    _g('add', '-A'); _g('commit', '-m', 'subproject commit xyz')
    out = brief.session_diff(actual, folder)
    joined = '\n'.join(out)
    assert 'service' in joined and 'subproject commit' in joined
