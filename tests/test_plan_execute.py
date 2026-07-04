import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, ENTER, ESC

from claude_sessions import plan_execute


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def test_plan_execute_happy_path(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    monkeypatch.setattr(plan_execute, '_plan',
                        lambda task, m, cwd: '1. do X\n2. verify Y')
    monkeypatch.setattr(plan_execute, 'get_claude_exe', lambda: r'C:\fake.exe',
                        raising=False)
    launched = {}
    import subprocess
    monkeypatch.setattr(subprocess, 'call',
                        lambda args, **k: launched.setdefault('args', args) or 0)
    # type the task, ENTER; then approve the plan in diffview (ENTER)
    keys = flat(typed('build a parser'), ENTER, ENTER)
    res, cap, _ = run_flow(monkeypatch, keys, plan_execute.run, actual, folder, 'alpha')
    assert res is True
    # plan saved to disk
    plan_path = os.path.join(actual, plan_execute.PLAN_FILE)
    assert os.path.isfile(plan_path) and 'do X' in open(plan_path, encoding='utf-8').read()
    # execution launched with exec model + append-system-prompt pointer
    args = launched['args']
    assert '--model' in args and 'claude-sonnet-5' in args
    assert '--append-system-prompt' in args
    ptr = args[args.index('--append-system-prompt') + 1]
    assert 'plan-latest.md' in ptr


def test_plan_execute_reject(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd: 'a plan')
    called = {}
    import subprocess
    monkeypatch.setattr(subprocess, 'call', lambda *a, **k: called.setdefault('x', 1))
    keys = flat(typed('task'), ENTER, ESC)   # reject in diffview
    res, cap, _ = run_flow(monkeypatch, keys, plan_execute.run, actual, folder, 'alpha')
    assert res is False and 'x' not in called


def test_plan_execute_empty_task(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    res, cap, _ = run_flow(monkeypatch, ESC, plan_execute.run, actual, folder, 'alpha')
    assert res is False
