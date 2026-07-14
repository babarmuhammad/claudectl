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
        ('new', dict(OPTS0), f'v6|P|E|new|-|-|-|-|-|{cfg}|-|-|-|-'),
        ('continue', dict(OPTS0, effort='low'),
         f'v6|P|E|continue|low|-|-|-|-|{cfg}|-|-|-|-'),
        ('resume:abc', dict(OPTS0, model='claude-fable-5', perm='dontAsk'),
         f'v6|P|E|resume:abc|-|claude-fable-5|dontAsk|-|-|{cfg}|-|-|-|-'),
        ('new', dict(OPTS0, name='N N', worktree='wt', agent='rev'),
         f'v6|P|E|new|-|-|-|N N|wt|{cfg}|rev|-|-|-'),
        ('new', dict(OPTS0, max_thinking='8000', subagent_model='claude-haiku-4-5'),
         f'v6|P|E|new|-|-|-|-|-|{cfg}|-|-|8000|claude-haiku-4-5'),
    ]
    for choice, opts, expected in cases:
        line = main_mod.build_choice_line('P', 'E', choice, opts)
        assert line == expected, (choice, line)


# ── F3: launch economy env + choice-line v6 ──────────────────

def env_of(call):
    return call[1].get('env', {})


def test_economy_env_injected(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'new',
                           {'max_thinking': '8000', 'subagent_model': 'claude-haiku-4-5'})
    env = env_of(call)
    assert env['MAX_THINKING_TOKENS'] == '8000'
    assert env['CLAUDE_CODE_SUBAGENT_MODEL'] == 'claude-haiku-4-5'


def test_economy_env_absent_when_unset(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    call = captured_launch(monkeypatch, sb, 'new', {})
    env = env_of(call)
    assert 'MAX_THINKING_TOKENS' not in env
    assert 'CLAUDE_CODE_SUBAGENT_MODEL' not in env


def test_choice_line_v6_round_trip(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    opts = dict(OPTS0, effort='high', model='claude-sonnet-5', cfgdir='C:/cfg',
                max_thinking='16000', subagent_model='claude-haiku-4-5',
                agent='', agents_json='')
    line = main_mod.build_choice_line('C:/proj', 'ENC', 'new', opts)
    assert line.startswith('v6|')
    p, enc, choice, got = main_mod.parse_choice_line(line)
    assert (p, enc, choice) == ('C:/proj', 'ENC', 'new')
    assert got['max_thinking'] == '16000'
    assert got['subagent_model'] == 'claude-haiku-4-5'
    assert got['effort'] == 'high'


def test_v5_line_parses_without_economy(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    v5 = 'v5|C:/proj|ENC|new|high|claude-sonnet-5|-|-|-|C:/cfg|-|-'
    p, enc, choice, got = main_mod.parse_choice_line(v5)
    assert (p, enc, choice) == ('C:/proj', 'ENC', 'new')
    assert got['max_thinking'] == '' and got['subagent_model'] == ''
    assert got['effort'] == 'high'
