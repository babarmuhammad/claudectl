"""Recent-work memory: heuristic capture, bounded ring buffer, digest budget,
and the hook installer round-trip.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox
from claude_sessions import worklog, hooks


def _transcript(path, title='', first_user='do the thing', edits=('a.py', 'b.py')):
    lines = []
    if title:
        lines.append({'type': 'ai-title', 'title': title})
    lines.append({'role': 'user', 'content': first_user})
    content = [{'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': f}}
               for f in edits]
    lines.append({'type': 'assistant', 'message': {'role': 'assistant', 'content': content}})
    with open(path, 'w', encoding='utf-8') as f:
        for o in lines:
            f.write(json.dumps(o) + '\n')


def test_summarize_transcript(tmp_path):
    tp = tmp_path / 's.jsonl'
    _transcript(str(tp), title='Add auth flow', edits=('login.py', 'auth.py'))
    summary, files = worklog.summarize_transcript(str(tp))
    assert summary == 'Add auth flow'
    assert files == ['auth.py', 'login.py']


def test_summarize_falls_back_to_first_user(tmp_path):
    tp = tmp_path / 's.jsonl'
    _transcript(str(tp), title='', first_user='fix the parser bug', edits=())
    summary, files = worklog.summarize_transcript(str(tp))
    assert summary == 'fix the parser bug' and files == []


def test_capture_and_ring_buffer(tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    # write more than CAP sessions
    for i in range(worklog.CAP + 4):
        tp = tmp_path / f's{i}.jsonl'
        _transcript(str(tp), title=f'session {i}', edits=(f'f{i}.py',))
        worklog.capture_session(str(proj), f'sid-{i}', str(tp))
    entries = worklog.load_worklog(str(proj))
    assert len(entries) == worklog.CAP                  # trimmed
    assert entries[-1]['summary'] == f'session {worklog.CAP + 3}'  # newest kept


def test_capture_dedups_by_session(tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    tp = tmp_path / 's.jsonl'
    _transcript(str(tp), title='first', edits=('a.py',))
    worklog.capture_session(str(proj), 'same-sid', str(tp))
    _transcript(str(tp), title='second', edits=('b.py',))
    worklog.capture_session(str(proj), 'same-sid', str(tp))
    entries = worklog.load_worklog(str(proj))
    assert len(entries) == 1 and entries[0]['summary'] == 'second'


def test_capture_skips_empty(tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    tp = tmp_path / 'e.jsonl'
    _transcript(str(tp), title='', first_user='', edits=())
    assert worklog.capture_session(str(proj), 'sid', str(tp)) is None
    assert worklog.load_worklog(str(proj)) == []


def test_render_digest_budget(tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    for i in range(6):
        worklog.add_entry(str(proj), {'session_id': f's{i}',
                                      'ended_at': '2026-07-20T10:00:00Z',
                                      'summary': f'work {i}', 'files': ['x.py']})
    dig = worklog.render_digest(str(proj))
    assert dig.startswith('## Recent work')
    assert 'work 5' in dig                              # newest first
    assert len(dig) <= worklog.DIGEST_BUDGET + 80       # within budget (+header)


def test_render_digest_empty(tmp_path):
    assert worklog.render_digest(str(tmp_path)) == ''


def test_worklog_hook_install_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(hooks, 'settings_path', str(tmp_path / 'settings.json'))
    assert not hooks.worklog_hook_installed()
    assert hooks.install_worklog_hook()
    assert hooks.worklog_hook_installed()
    # both events registered
    d = json.load(open(tmp_path / 'settings.json', encoding='utf-8'))
    assert 'SessionStart' in d['hooks'] and 'Stop' in d['hooks']
    # idempotent
    hooks.install_worklog_hook()
    assert hooks.worklog_hook_installed()
    # uninstall
    assert hooks.uninstall_worklog_hook()
    assert not hooks.worklog_hook_installed()
