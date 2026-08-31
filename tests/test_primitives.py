"""Gates for the shared primitives.

Each of these exists because the thing it forbids was written 3-8 times, and
each new POSIX branch would otherwise have to be written that many times too.
"""

import ast
import io
import json
import os

import pytest

from claude_sessions import jsonstore, proc, store, transcripts

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'claude_sessions')


def _modules():
    for name in sorted(os.listdir(SRC)):
        if name.endswith('.py'):
            yield name, io.open(os.path.join(SRC, name), encoding='utf-8').read()


# ── transcripts.iter_json ────────────────────────────────────

def _write(tmp_path, name, objs):
    p = tmp_path / name
    p.write_text('\n'.join(json.dumps(o) for o in objs), encoding='utf-8')
    return str(p)


def test_iter_json_skips_blank_and_unparseable_lines(tmp_path):
    p = tmp_path / 's.jsonl'
    p.write_text('{"a":1}\n\n   \nnot json\n{"a":2}\n', encoding='utf-8')
    assert [o['a'] for o in transcripts.iter_json(str(p))] == [1, 2]


def test_iter_json_pages_over_objects_not_lines(tmp_path):
    p = tmp_path / 's.jsonl'
    p.write_text('{"a":1}\n\nnot json\n{"a":2}\n{"a":3}\n', encoding='utf-8')
    got = [o['a'] for o in transcripts.iter_json(str(p), offset=1, limit=1)]
    assert got == [2]


def test_iter_json_stops_at_max_bytes(tmp_path):
    p = _write(tmp_path, 's.jsonl', [{'a': i} for i in range(100)])
    got = list(transcripts.iter_json(p, max_bytes=40))
    assert 0 < len(got) < 100


def test_iter_json_prefilter_skips_before_parsing(tmp_path):
    p = tmp_path / 's.jsonl'
    p.write_text('{"name":"Bash"}\n{"name":"Read"}\n', encoding='utf-8')
    got = list(transcripts.iter_json(str(p), prefilter='"Bash"'))
    assert [o['name'] for o in got] == ['Bash']


def test_iter_json_is_a_generator_not_a_readlines(tmp_path):
    """The whole point: nothing may materialise the file."""
    p = _write(tmp_path, 's.jsonl', [{'a': i} for i in range(10)])
    it = transcripts.iter_json(p)
    assert next(it)['a'] == 0        # first object before the file is exhausted
    it.close()


def test_a_missing_transcript_is_empty_not_an_error(tmp_path):
    assert list(transcripts.iter_json(str(tmp_path / 'nope.jsonl'))) == []


def test_no_module_reads_a_transcript_with_readlines():
    offenders = []
    for name, src in _modules():
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'readlines'):
                offenders.append('%s:%d' % (name, node.lineno))
    assert not offenders, 'readlines() on a transcript: ' + ', '.join(offenders)


# ── store.project_folder ─────────────────────────────────────

def test_project_folder_joins_under_projects():
    got = store.project_folder(os.path.join('C:', 'cfg'), 'D--Claude')
    assert got.endswith(os.path.join('projects', 'D--Claude'))


@pytest.mark.parametrize('enc', [
    '..', '../../etc', r'..\..\windows', 'a/b', r'a\b', '', 'a.b', 'a b',
    'C:', 'pro\x00jects',
])
def test_project_folder_refuses_anything_the_encoder_could_not_produce(enc):
    with pytest.raises(ValueError):
        store.project_folder('cfg', enc)


def test_session_file_validates_the_session_id_too():
    with pytest.raises(ValueError):
        store.session_file('cfg', 'D--Claude', '../../secret')


def test_no_module_hand_builds_a_project_folder():
    """`os.path.join(<anything>, 'projects', ...)` is store.py's job. Fourteen
    hand-written copies is fourteen places for the containment check to be
    missing — which is exactly how `gui_api._folder` reached ~40 endpoints
    with an unnormalised join."""
    offenders = []
    for name, src in _modules():
        if name in ('store.py', 'config.py'):     # config defines the root
            continue
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'join'):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == 'projects':
                    offenders.append('%s:%d' % (name, node.lineno))
    assert not offenders, 'hand-built projects join: ' + ', '.join(offenders)


# ── jsonstore ────────────────────────────────────────────────

def test_absent_file_is_the_default(tmp_path):
    assert jsonstore.load(str(tmp_path / 'nope.json'), expect=dict) == {}
    assert jsonstore.load(str(tmp_path / 'nope.json'), expect=list) == []


def test_a_corrupt_file_is_preserved_not_replaced(tmp_path):
    p = tmp_path / 'graph.json'
    p.write_text('{"entities": [1,2,3', encoding='utf-8')      # truncated write
    assert jsonstore.load(str(p), expect=dict) == {}
    assert not p.exists(), 'the corrupt file must be moved aside'
    moved = [f for f in os.listdir(tmp_path) if '.corrupt-' in f]
    assert moved, 'no quarantine copy was kept'
    assert io.open(tmp_path / moved[0], encoding='utf-8').read() == '{"entities": [1,2,3'


def test_the_wrong_top_level_type_counts_as_corrupt(tmp_path):
    p = tmp_path / 'w.json'
    p.write_text('{"not": "a list"}', encoding='utf-8')
    assert jsonstore.load(str(p), expect=list) == []
    assert [f for f in os.listdir(tmp_path) if '.corrupt-' in f]


def test_a_default_is_copied_not_shared(tmp_path):
    d = {'a': 1}
    got = jsonstore.load(str(tmp_path / 'nope.json'), default=d)
    got['a'] = 2
    assert d['a'] == 1


def test_hooks_load_does_not_erase_a_corrupt_settings_file(tmp_path):
    """The one that matters: every writer read-modify-writes Claude Code's own
    settings.json, so treating an unparseable one as {} deletes the user's
    hooks, permissions and outputStyle on the next save."""
    from claude_sessions import hooks
    p = tmp_path / 'settings.json'
    p.write_text('{"hooks": {"Stop": [', encoding='utf-8')
    assert hooks._load(str(tmp_path)) == {}
    assert not p.exists()
    assert [f for f in os.listdir(tmp_path) if '.corrupt-' in f]


# ── proc ─────────────────────────────────────────────────────

def test_pid_alive_knows_about_this_process():
    assert proc.pid_alive(os.getpid()) is True
    assert proc.pid_alive(0) is False
    assert proc.pid_alive('nonsense') is False


def test_run_pins_the_encoding_so_a_non_ascii_path_cannot_raise(tmp_path):
    """`text=True` alone decodes with the locale codepage. One accented branch
    name then raises inside subprocess and the caller concludes 'not a repo' —
    a real, separately-diagnosed cause of the worktrees bug."""
    import sys
    # the child writes real UTF-8 bytes; with text=True and no encoding the
    # parent would decode them as cp1252 and hand back mojibake
    r = proc.run([sys.executable, '-c',
                  r'import sys;sys.stdout.buffer.write("caffè — naïve".encode("utf-8"))'])
    assert r is not None and 'caffè — naïve' in r.stdout


def test_git_returns_none_rather_than_raising_when_git_fails(tmp_path):
    assert proc.git(['rev-parse', 'HEAD'], str(tmp_path)) is None


def test_no_module_spawns_a_terminal_directly():
    """Four hand-rolled spawns is four places to write the POSIX branch."""
    offenders = []
    for name, src in _modules():
        if name == 'proc.py':
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == 'shell' and getattr(kw.value, 'value', False) is True:
                    offenders.append('%s:%d shell=True' % (name, node.lineno))
                if kw.arg == 'creationflags' and 'CREATE_NEW_CONSOLE' in ast.dump(kw.value):
                    offenders.append('%s:%d CREATE_NEW_CONSOLE' % (name, node.lineno))
            for arg in node.args:
                if isinstance(arg, ast.List) and arg.elts:
                    first = arg.elts[0]
                    if (isinstance(first, ast.Constant) and first.value == 'cmd'
                            and any(isinstance(e, ast.Constant) and e.value == 'start'
                                    for e in arg.elts)):
                        offenders.append('%s:%d cmd /c start' % (name, node.lineno))
    # proxy_base spawns the detached proxy consoles and documents why: they must
    # outlive claudectl, and the routing log IS the feature. ONE spawn shared by
    # both daemons (failover, gateway) — which is the point this gate is making.
    offenders = [o for o in offenders if not o.startswith('proxy_base.py')]
    assert not offenders, 'terminal spawned outside proc.py: ' + ', '.join(offenders)


def test_only_one_git_wrapper_runs_a_subprocess():
    """repos._git and review._git may keep their names and argument orders,
    but both must be one line over proc.git."""
    offenders = []
    for name, src in _modules():
        if name == 'proc.py':
            continue
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'subprocess'):
                continue
            for arg in node.args:
                head = arg
                if isinstance(arg, ast.BinOp):
                    head = arg.left
                if (isinstance(head, ast.List) and head.elts
                        and isinstance(head.elts[0], ast.Constant)
                        and head.elts[0].value == 'git'):
                    offenders.append('%s:%d' % (name, node.lineno))
    assert not offenders, 'git spawned outside proc.py: ' + ', '.join(offenders)


# ── the find_actual_path memo ────────────────────────────────

def test_find_actual_path_does_not_reread_an_unchanged_folder(tmp_path, monkeypatch):
    """It is called once per project by /api/state, /api/search-index, every
    usage endpoint and a dashboard that polls every 10s, and each call opens
    up to three transcripts."""
    from claude_sessions import paths
    folder = tmp_path / 'D--proj'
    folder.mkdir()
    (folder / 'a.jsonl').write_text(json.dumps({'cwd': str(tmp_path)}) + '\n',
                                    encoding='utf-8')
    paths._path_cache.clear()

    calls = []
    real = paths.path_from_transcripts
    monkeypatch.setattr(paths, 'path_from_transcripts',
                        lambda f, **kw: (calls.append(f), real(f, **kw))[1])

    first = paths.find_actual_path('D--proj', folder=str(folder))
    second = paths.find_actual_path('D--proj', folder=str(folder))
    assert first == second == str(tmp_path)
    assert len(calls) == 1, 'the second call re-read the folder'


def test_the_memo_expires_when_the_folder_changes(tmp_path, monkeypatch):
    from claude_sessions import paths
    folder = tmp_path / 'D--proj'
    folder.mkdir()
    (folder / 'a.jsonl').write_text(json.dumps({'cwd': str(tmp_path)}) + '\n',
                                    encoding='utf-8')
    paths._path_cache.clear()
    paths.find_actual_path('D--proj', folder=str(folder))
    # a new session lands in the folder -> its mtime moves -> re-resolve
    paths._path_cache[str(folder)] = (0, 'STALE')
    assert paths.find_actual_path('D--proj', folder=str(folder)) == str(tmp_path)


def test_the_debug_dump_into_temp_is_gone():
    """An unconditional %TEMP%\\ai_analyze_debug.jsonl, opened with an
    unguarded os.environ['TEMP'] and never closed on the error path."""
    src = io.open(os.path.join(SRC, 'claude_md.py'), encoding='utf-8').read()
    assert 'ai_analyze_debug' not in src


def test_no_module_builds_its_own_headless_claude_call():
    """Four modules hand-rolled `claude --print <prompt>` instead of calling
    memory._claude_stdin. Each was a place the provider env, the
    --max-budget-usd cap and the Windows command-line length limit had to be
    remembered separately -- and none of the four remembered any of them."""
    offenders = []
    for name, src in _modules():
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.List) and node.elts):
                continue
            flags = {e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if flags & {'-p', '--print'}:
                offenders.append('%s:%d' % (name, node.lineno))
    # memory.py IS the seam. The other two stream their output as it arrives
    # (--output-format stream-json / a live progress pane), which is a different
    # contract from "give me the finished text", not a second copy of one.
    waived = ('memory.py', 'claude_md.py', 'plan_execute.py')
    offenders = [o for o in offenders if not o.startswith(waived)]
    assert not offenders, ('headless claude spawned outside memory._claude_stdin: '
                           + ', '.join(offenders))
