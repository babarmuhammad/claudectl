import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from harness import Sandbox

from claude_sessions import main as main_mod


OPTS0 = {'effort': '', 'model': '', 'perm': '', 'name': '', 'worktree': '', 'agent': ''}


def captured_launch(monkeypatch, sb, choice, opts, folder_files=None,
                    encoded='X--work-proj'):
    """Run _direct_launch with subprocess.call captured. Returns (args, kwargs)."""
    calls = []
    import subprocess

    def fake_call(*a, **kw):
        calls.append((a, kw))
        return 0
    monkeypatch.setattr(subprocess, 'call', fake_call)
    monkeypatch.setattr(main_mod, 'get_claude_exe', lambda: r'C:\fake\claude.exe')
    folder = sb.projects / encoded
    folder.mkdir(exist_ok=True)
    for fname, content in (folder_files or {}).items():
        (folder / fname).write_text(content, encoding='utf-8')
    main_mod._direct_launch(str(sb.root), encoded, choice, dict(OPTS0, **opts))
    return calls[0]


def argv_of(call):
    return call[0][0]


def test_direct_launch_new_plain(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'new', {})
    assert argv_of(call) == [r'C:\fake\claude.exe']


def test_direct_launch_resume(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'resume:abc-123', {})
    assert argv_of(call)[1:3] == ['-r', 'abc-123']


def test_direct_launch_resume_named(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'resume-named::abc::My Name', {})
    assert argv_of(call)[1:3] == ['-r', 'abc']


def test_direct_launch_fork(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'fork:abc', {})
    argv = argv_of(call)
    assert argv[1:3] == ['-r', 'abc'] and '--fork-session' in argv


def test_direct_launch_continue(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'continue', {})
    assert argv_of(call)[1] == '-c'


def test_direct_launch_agent(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'resume:abc', {'agent': 'reviewer'})
    argv = argv_of(call)
    assert '--agent' in argv and argv[argv.index('--agent') + 1] == 'reviewer'


def test_direct_launch_all_flags(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    dir1 = str(sb.root)   # exists
    call = captured_launch(
        monkeypatch, sb, 'new',
        {'effort': 'high', 'model': 'claude-sonnet-4-6', 'perm': 'plan',
         'name': 'Sess', 'worktree': '*'},
        folder_files={'system-prompt.txt': 'sp', 'add-dirs.txt': dir1})
    argv = argv_of(call)
    s = ' '.join(argv)
    assert '--effort high' in s
    assert '--model claude-sonnet-4-6' in s
    assert '--permission-mode plan' in s
    assert '-n Sess' in s
    assert '-w' in argv
    assert '--system-prompt-file' in s
    assert '--add-dir' in s and dir1 in argv


def test_direct_launch_worktree_custom(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'new', {'worktree': 'feat-x'})
    argv = argv_of(call)
    i = argv.index('-w')
    assert argv[i + 1] == 'feat-x'


def test_direct_launch_name_only_for_new(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'resume:abc',
                           {'name': 'X', 'worktree': '*'})
    argv = argv_of(call)
    assert '-n' not in argv and '-w' not in argv


def test_direct_launch_terminal(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'terminal', {})
    assert call[0][0] == 'cmd /k'
    assert call[1].get('shell') is True


def test_direct_launch_cwd_and_extra_paths(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    extra = str(sb.root)
    call = captured_launch(monkeypatch, sb, 'new', {},
                           folder_files={'extra-paths.txt': extra})
    kw = call[1]
    assert kw['cwd'] == str(sb.root)
    assert kw['env']['PATH'].startswith(extra + ';')


def test_choice_line_matrix(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)          # pins main_mod.config_dir
    cfg = str(sb.cfg)
    cases = [
        ('new', dict(OPTS0), f'v5|P|E|new|-|-|-|-|-|{cfg}|-|-'),
        ('continue', dict(OPTS0, effort='low'),
         f'v5|P|E|continue|low|-|-|-|-|{cfg}|-|-'),
        ('resume:abc', dict(OPTS0, model='claude-fable-5', perm='dontAsk'),
         f'v5|P|E|resume:abc|-|claude-fable-5|dontAsk|-|-|{cfg}|-|-'),
        ('new', dict(OPTS0, name='N N', worktree='wt', agent='rev'),
         f'v5|P|E|new|-|-|-|N N|wt|{cfg}|rev|-'),
    ]
    for choice, opts, expected in cases:
        line = main_mod.build_choice_line('P', 'E', choice, opts)
        assert line == expected, (choice, line)
