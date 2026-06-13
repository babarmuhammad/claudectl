import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, DOWN, ENTER, ESC

from claude_sessions import sessions, claude_md


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def _write_jsonl(path, objs):
    with open(path, 'w', encoding='utf-8') as f:
        for o in objs:
            f.write(json.dumps(o) + '\n')


# ── changed files ────────────────────────────────────────────

def test_session_changed_files(tmp_path):
    p = tmp_path / 's.jsonl'
    _write_jsonl(p, [
        {'message': {'content': [
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'a.py'}}]}},
        {'message': {'content': [
            {'type': 'tool_use', 'name': 'Write', 'input': {'file_path': 'b.py'}},
            {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': 'a.py'}}]}},
        {'message': {'content': [
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}]}},
    ])
    changed = sessions.session_changed_files(str(p))
    assert changed[0] == ('a.py', 2)      # most-edited first
    assert ('b.py', 1) in changed
    assert all('command' not in f for f, _ in changed)   # bash ignored


def test_changed_files_empty(tmp_path):
    p = tmp_path / 's.jsonl'
    _write_jsonl(p, [{'role': 'user', 'content': 'hi'}])
    assert sessions.session_changed_files(str(p)) == []


# ── tags ─────────────────────────────────────────────────────

def test_tags_roundtrip(tmp_path):
    d = str(tmp_path)
    sessions.save_tags(d, {'sid1': ['bug', 'wip']})
    assert sessions.load_tags(d) == {'sid1': ['bug', 'wip']}


def test_tags_corrupt(tmp_path):
    open(os.path.join(str(tmp_path), 'tags.json'), 'w').write('{{bad')
    assert sessions.load_tags(str(tmp_path)) == {}


# ── per-session agents persistence ───────────────────────────

def test_session_agents_roundtrip(tmp_path):
    d = str(tmp_path)
    sessions.save_session_agents(d, 'sid1', ['01-core/rev', '02-lang/py'])
    assert sessions.load_session_agents(d)['sid1'] == ['01-core/rev', '02-lang/py']
    # empty clears the key
    sessions.save_session_agents(d, 'sid1', [])
    assert 'sid1' not in sessions.load_session_agents(d)


# ── memory map ───────────────────────────────────────────────

def test_resolve_memory_files(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(claude_md, 'config_dir', str(sb.cfg))
    proj = tmp_path / 'proj'
    proj.mkdir()
    (sb.cfg / 'CLAUDE.md').write_text('user mem', encoding='utf-8')
    (proj / 'CLAUDE.md').write_text('proj mem\nSee @docs/extra.md', encoding='utf-8')
    rows = claude_md.resolve_memory_files(str(proj))
    by_label = {r[0]: r for r in rows}
    assert by_label['user'][2] is True          # exists
    assert by_label['project'][2] is True
    assert by_label['local'][2] is False
    # @import detected, target missing
    proj_imports = by_label['project'][3]
    assert proj_imports and proj_imports[0][0] == 'docs/extra.md'
    assert proj_imports[0][1] is False           # not on disk


def test_memory_map_menu_opens_editor(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(claude_md, 'config_dir', str(sb.cfg))
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'CLAUDE.md').write_text('hello', encoding='utf-8')
    # nav to the project CLAUDE.md row (2nd) and open
    keys = flat(DOWN, ENTER, ESC)
    run_flow(monkeypatch, keys, claude_md.memory_map_menu, str(proj), 'proj')
    assert sb.editor_opened
