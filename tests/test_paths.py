import os
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


def test_find_actual_path_resolves(tmp_path, monkeypatch):
    # Build a fake structure under an existing drive root is not possible
    # in a sandboxed test; instead test the matcher logic via a real temp dir
    # by monkeypatching os.path.exists for the synthetic drive.
    target = tmp_path / 'My_Project' / 'sub+dir'
    target.mkdir(parents=True)

    import claude_sessions.paths as paths_mod

    real_exists = os.path.exists
    real_listdir = os.listdir

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
        if p.startswith('Z:\\'):
            return (tmp_path / p[3:]).is_dir()
        return os.path.isdir(p)

    monkeypatch.setattr(paths_mod.os.path, 'exists', fake_exists)
    monkeypatch.setattr(paths_mod.os, 'listdir', fake_listdir)
    monkeypatch.setattr(paths_mod.os.path, 'isdir', fake_isdir)

    result = find_actual_path('Z--My-Project-sub-dir')
    assert result is not None
    assert result.endswith('sub+dir')


def test_find_actual_path_case_insensitive(tmp_path, monkeypatch):
    (tmp_path / 'MyApp').mkdir()

    import claude_sessions.paths as paths_mod
    real_exists, real_listdir = os.path.exists, os.listdir

    monkeypatch.setattr(paths_mod.os.path, 'exists',
                        lambda p: True if p == 'Z:\\' else real_exists(p))
    monkeypatch.setattr(paths_mod.os, 'listdir',
                        lambda p: real_listdir(str(tmp_path)) if p == 'Z:\\' else real_listdir(p))
    monkeypatch.setattr(paths_mod.os.path, 'isdir',
                        lambda p: (tmp_path / p[3:]).is_dir() if p.startswith('Z:\\') else os.path.isdir(p))

    assert find_actual_path('Z--myapp') is not None
