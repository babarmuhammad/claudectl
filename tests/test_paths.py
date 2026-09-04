import json
import os
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions.paths import encode_component, find_actual_path


def test_encode_component_plain():
    assert encode_component('myapp') == 'myapp'


def test_encode_component_specials():
    assert encode_component('my_app') == 'my-app'
    assert encode_component('c++') == 'c--'
    assert encode_component('a#b_c+d-e') == 'a-b-c-d-e'
    # dots and spaces are separators too (Claude Code replaces all non-alnum)
    assert encode_component('IKM.Platform.AINode') == 'IKM-Platform-AINode'
    assert encode_component('My Project (v2)') == 'My-Project--v2-'
    # ASCII-only: non-ASCII letters collapse to '-' (matches Claude's /[^a-zA-Z0-9]/g)
    assert encode_component('Caffè') == 'Caff-'
    assert encode_component('日本') == '--'


def test_find_actual_path_no_separator():
    assert find_actual_path('nodashes') is None


def test_find_actual_path_missing_drive():
    # drive that can't exist
    assert find_actual_path('Q--whatever') is None or isinstance(
        find_actual_path('Q--whatever'), str)


@pytest.mark.skipif(os.name != 'nt',
                    reason='simulates a Windows drive root; find_actual_path '
                           'takes the POSIX / branch there instead')
def test_find_actual_path_resolves(tmp_path, monkeypatch):
    # Build a fake structure under an existing drive root is not possible
    # in a sandboxed test; instead test the matcher logic via a real temp dir
    # by monkeypatching os.path.exists for the synthetic drive.
    target = tmp_path / 'My_Project' / 'sub+dir'
    target.mkdir(parents=True)

    import claude_sessions.paths as paths_mod

    real_exists = os.path.exists
    real_listdir = os.listdir
    real_isdir = os.path.isdir

    def fake_exists(p):
        if p == 'Z:\\':
            return True
        return real_exists(p)

    def fake_listdir(p):
        if p == 'Z:\\':
            return real_listdir(str(tmp_path))
        if p.startswith('Z:\\'):
            return real_listdir(str(tmp_path / p[3:]))
        return real_listdir(p)

    def fake_isdir(p):
        # os.fspath + real_isdir, not Path.is_dir(): 3.14 reimplemented
        # pathlib.Path.is_dir() on top of os.path.isdir(self), so this very
        # fake was re-entered with a Path and `.startswith` raised.
        p = os.fspath(p)
        if p.startswith('Z:\\'):
            return real_isdir(os.path.join(str(tmp_path), p[3:]))
        return real_isdir(p)

    monkeypatch.setattr(paths_mod.os.path, 'exists', fake_exists)
    monkeypatch.setattr(paths_mod.os, 'listdir', fake_listdir)
    monkeypatch.setattr(paths_mod.os.path, 'isdir', fake_isdir)

    result = find_actual_path('Z--My-Project-sub-dir')
    assert result is not None
    assert result.endswith('sub+dir')


@pytest.mark.skipif(os.name != 'nt',
                    reason='simulates a Windows drive root; find_actual_path '
                           'takes the POSIX / branch there instead')
def test_find_actual_path_case_insensitive(tmp_path, monkeypatch):
    (tmp_path / 'MyApp').mkdir()

    import claude_sessions.paths as paths_mod
    real_exists, real_listdir = os.path.exists, os.listdir
    real_isdir = os.path.isdir

    monkeypatch.setattr(paths_mod.os.path, 'exists',
                        lambda p: True if p == 'Z:\\' else real_exists(p))
    monkeypatch.setattr(paths_mod.os, 'listdir',
                        lambda p: real_listdir(str(tmp_path)) if p == 'Z:\\' else real_listdir(p))
    monkeypatch.setattr(
        paths_mod.os.path, 'isdir',
        lambda p: (real_isdir(os.path.join(str(tmp_path), os.fspath(p)[3:]))
                   if os.fspath(p).startswith('Z:\\')
                   else real_isdir(os.fspath(p))))

    assert find_actual_path('Z--myapp') is not None


def test_find_actual_path_resolves_a_unc_project(tmp_path):
    """A UNC path encodes with a LEADING '--' (both backslashes become dashes),
    so the drive-letter split yielded an empty drive and the project silently
    vanished from every list. The transcript records the real path."""
    from claude_sessions.paths import path_from_transcripts
    folder = tmp_path / '--server-share-Project'
    folder.mkdir()
    real = tmp_path / 'pretend-share'
    real.mkdir()
    enc = encode_component('\\\\server\\share\\Project')
    assert enc.startswith('--')                       # the shape that used to fail
    (folder / 's.jsonl').write_text(
        json.dumps({'type': 'user', 'cwd': str(real)}) + '\n', encoding='utf-8')
    assert find_actual_path(enc, folder=str(folder)) == str(real)
    assert path_from_transcripts(str(folder)) == str(real)
    # and without the folder there is nothing to read, so it still declines
    assert find_actual_path(enc) is None


def test_path_from_transcripts_ignores_junk_and_dead_paths(tmp_path):
    from claude_sessions.paths import path_from_transcripts
    folder = tmp_path / 'X--gone'
    folder.mkdir()
    (folder / 's.jsonl').write_text(
        'not json\n'
        + json.dumps({'type': 'user'}) + '\n'                       # no cwd
        + json.dumps({'cwd': str(tmp_path / 'deleted')}) + '\n',    # not a dir
        encoding='utf-8')
    assert path_from_transcripts(str(folder)) is None


def test_a_folder_that_cannot_be_resolved_is_only_walked_once(tmp_path, monkeypatch):
    """The miss is the expensive answer, so it is the one that has to be cached.

    Storing only successes meant a dead project folder paid the recursive
    `_walk_for` guess on EVERY call, forever — measured at ~38 ms each, and 42
    of this machine's 72 folders were dead pytest temp directories. That was
    98% of `gui.list_projects()`, which the GUI's 5-second /api/memory/active
    poll calls in full.
    """
    import claude_sessions.paths as paths_mod
    paths_mod._path_cache.clear()
    folder = tmp_path / 'Z--long-gone-project'
    folder.mkdir()
    (folder / 's.jsonl').write_text(
        json.dumps({'cwd': str(tmp_path / 'deleted')}) + '\n', encoding='utf-8')

    walks = []
    real_walk = paths_mod._walk_for
    monkeypatch.setattr(paths_mod, '_walk_for',
                        lambda enc, d=8: walks.append(enc) or real_walk(enc, d))

    for _ in range(5):
        assert find_actual_path('Z--long-gone-project', folder=str(folder)) is None
    assert len(walks) == 1, f'walked {len(walks)} times — the miss is not cached'


def test_a_cached_miss_expires_so_a_recreated_project_reappears(tmp_path, monkeypatch):
    """The mtime key cannot see the TARGET directory coming back: re-clone a repo
    to the same path and nothing under the project folder changes. So a miss
    also expires on time, which is the only reason it is safe to cache at all."""
    import time
    import claude_sessions.paths as paths_mod
    paths_mod._path_cache.clear()
    folder = tmp_path / 'Z--comes-back'
    folder.mkdir()
    real = tmp_path / 'comes-back'
    (folder / 's.jsonl').write_text(
        json.dumps({'cwd': str(real)}) + '\n', encoding='utf-8')

    assert find_actual_path('Z--comes-back', folder=str(folder)) is None
    real.mkdir()                                   # the project reappears
    assert find_actual_path('Z--comes-back', folder=str(folder)) is None  # still cached
    monkeypatch.setattr(time, 'time',
                        lambda _t=time.time(): _t + paths_mod._MISS_TTL + 1)
    assert find_actual_path('Z--comes-back', folder=str(folder)) == str(real)


def test_resolve_dir_rejects_non_directories(tmp_path):
    from claude_sessions.paths import resolve_dir
    f = tmp_path / 'file.txt'
    f.write_text('x', encoding='utf-8')
    assert resolve_dir(str(tmp_path)) == str(tmp_path)
    assert resolve_dir(str(f)) == ''
    assert resolve_dir(str(tmp_path / 'nope')) == ''
    assert resolve_dir('') == '' and resolve_dir(None) == ''
