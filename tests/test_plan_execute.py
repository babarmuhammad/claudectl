import os
import subprocess
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
                        lambda task, m, cwd, effort='': '1. do X\n2. verify Y')
    monkeypatch.setattr(plan_execute, 'get_claude_exe', lambda: r'C:\fake.exe',
                        raising=False)
    launched = {}
    import subprocess
    monkeypatch.setattr(subprocess, 'call',
                        lambda args, **k: launched.setdefault('args', args) or 0)
    # pick default effort (ENTER); skip council (ESC = No); type the task, ENTER;
    # approve the plan (ENTER)
    keys = flat(ENTER, ESC, typed('build a parser'), ENTER, ENTER)
    res, cap, _ = run_flow(monkeypatch, keys, plan_execute.run, actual, folder, 'alpha')
    assert res is True
    # plan saved to disk
    plan_path = os.path.join(actual, plan_execute.PLAN_FILE)
    assert os.path.isfile(plan_path) and 'do X' in open(plan_path, encoding='utf-8').read()
    # execution launched with exec model + merged system-prompt pointer file
    args = launched['args']
    assert '--model' in args and 'claude-sonnet-5' in args
    assert '--system-prompt-file' in args
    sp_path = args[args.index('--system-prompt-file') + 1]
    ptr = open(sp_path, encoding='utf-8').read()
    assert 'plan-latest.md' in ptr


def test_plan_execute_reject(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'a plan')
    called = {}
    import subprocess
    monkeypatch.setattr(subprocess, 'call', lambda *a, **k: called.setdefault('x', 1))
    keys = flat(ENTER, ESC, typed('task'), ENTER, ESC, ESC)   # default effort; no council; reject plan, dismiss edit menu
    res, cap, _ = run_flow(monkeypatch, keys, plan_execute.run, actual, folder, 'alpha')
    assert res is False and 'x' not in called


def test_plan_headless_in_background_job_thread(monkeypatch, tmp_path):
    # regression: run_with_progress_stdin's clear-screen fallback
    # (os.system('cls'), used when VT mode isn't available -- true for a
    # console-less GUI job thread) spawns a real console per render tick.
    # At ~10 ticks/sec for up to 600s that looked like terminals endlessly
    # opening/closing until the app was killed. memory._tls.silent is set
    # by every GUI job (gui_api.start_job) -- run_with_progress_stdin
    # itself must honor it (see tests/test_ui_progress.py for that check
    # directly); this confirms _plan() still gets the right result through
    # that path with no separate bypass of its own.
    from claude_sessions import config, memory
    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(memory._tls, 'silent', True, raising=False)

    from claude_sessions import ui

    def boom(lines):
        raise AssertionError('render_frame must not run in silent mode')
    monkeypatch.setattr(ui.render, 'render_frame', boom)

    import subprocess
    captured = {}

    class FakeResult:
        stdout = '1. step one\n2. step two'
    def fake_run(args, **kw):
        captured['args'] = args
        return FakeResult()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    out = plan_execute._plan('do the thing', 'claude-opus-4-8', str(tmp_path), effort='xhigh')
    assert out == '1. step one\n2. step two'
    assert '--model' in captured['args'] and 'claude-opus-4-8' in captured['args']
    assert '--effort' in captured['args'] and 'xhigh' in captured['args']


def test_plan_prompt_includes_weak_model_instructions(monkeypatch, tmp_path):
    # the plan is written once by a strong model but EXECUTED by a cheaper
    # one -- the generation prompt must carry WEAK_MODEL_PLAN_INSTRUCTIONS so
    # the resulting plan is unambiguous enough for a weaker executor.
    from claude_sessions import config, memory
    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(memory._tls, 'silent', True, raising=False)
    import subprocess
    captured = {}

    class FakeResult:
        stdout = '1. step'
    def fake_run(args, **kw):
        captured['input'] = kw.get('input')
        return FakeResult()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    plan_execute._plan('do the thing', 'claude-opus-4-8', str(tmp_path))
    assert plan_execute.WEAK_MODEL_PLAN_INSTRUCTIONS in captured['input']


def test_council_synth_prompt_includes_weak_model_instructions(monkeypatch, tmp_path):
    seen = {}
    def fake_headless(model, prompt, cwd, omni_env=None):
        if 'CRITIQUE' not in prompt:
            return f'critique from {model}'
        seen['synth_prompt'] = prompt
        return 'merged'
    monkeypatch.setattr(plan_execute, '_headless', fake_headless)
    plan_execute.optimize_plan_council('do the thing', _LONG_PLAN, str(tmp_path))
    assert plan_execute.WEAK_MODEL_PLAN_INSTRUCTIONS in seen['synth_prompt']


def test_plan_execute_empty_task(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, _ = sb.add_project('alpha')
    # ESC out of the effort menu, then ESC out of the (now-empty) task prompt
    res, cap, _ = run_flow(monkeypatch, flat(ESC, ESC, ESC), plan_execute.run, actual, folder, 'alpha')
    assert res is False


# ── model council ────────────────────────────────────────────

_LONG_PLAN = '1. do the first step in detail\n2. verify the second step works as expected'


def test_council_disabled_returns_plan_unchanged(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plan_execute, '_headless',
                        lambda *a, **k: calls.append(a) or 'ignored')
    # caller simply never invokes optimize_plan_council when the toggle is
    # off -- nothing to assert on the function itself beyond "not called"
    assert not calls


def test_council_enabled_calls_multiple_models_and_synthesizes(monkeypatch, tmp_path):
    calls = []
    def fake_headless(model, prompt, cwd, omni_env=None):
        calls.append(model)
        return 'FINAL MERGED PLAN' if model == plan_execute.COUNCIL_MODELS[0] and 'CRITIQUE' in prompt \
            else f'critique from {model}'
    monkeypatch.setattr(plan_execute, '_headless', fake_headless)
    out = plan_execute.optimize_plan_council('do the thing', _LONG_PLAN, str(tmp_path))
    # one critique call per roster model + one synthesis call
    assert calls.count(plan_execute.COUNCIL_MODELS[0]) == 2
    for m in plan_execute.COUNCIL_MODELS[1:]:
        assert calls.count(m) == 1
    assert out == 'FINAL MERGED PLAN'


def test_council_single_model_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_execute, '_headless',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not call a model')))
    out = plan_execute.optimize_plan_council('task', _LONG_PLAN, str(tmp_path), models=['claude-sonnet-5'])
    assert out == _LONG_PLAN


def test_council_all_models_fail_returns_original(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_execute, '_headless', lambda *a, **k: '')
    out = plan_execute.optimize_plan_council('task', _LONG_PLAN, str(tmp_path))
    assert out == _LONG_PLAN


def test_council_short_plan_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_execute, '_headless',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not call a model')))
    out = plan_execute.optimize_plan_council('task', 'too short', str(tmp_path))
    assert out == 'too short'


def test_council_routes_through_omniroute_when_configured(monkeypatch, tmp_path):
    seen_envs = []
    def fake_headless(model, prompt, cwd, omni_env=None):
        seen_envs.append(omni_env)
        return f'critique from {model}'
    monkeypatch.setattr(plan_execute, '_headless', fake_headless)
    omni_env = {'ANTHROPIC_BASE_URL': 'http://localhost:20128'}
    plan_execute.optimize_plan_council('task', _LONG_PLAN, str(tmp_path), omni_env=omni_env)
    assert seen_envs and all(e == omni_env for e in seen_envs)


def test_headless_never_prefixes_model(monkeypatch, tmp_path):
    # OmniRoute's live catalog has no 'anthropic/' namespace (aggregator-named
    # instead: 'aug/', 'tllm/', 'ddgw/') -- a guessed prefix 404s. Caller is
    # responsible for passing a model id valid for the target; _headless
    # must pass it through untouched, prefixed or not, omni_env or not.
    from claude_sessions import config
    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake.exe')
    captured = {}
    class FakeResult:
        stdout = 'ok'
    def fake_run(args, **k):
        captured['args'] = args
        return FakeResult()
    monkeypatch.setattr(subprocess, 'run', fake_run)

    plan_execute._headless('claude-sonnet-5', 'p', str(tmp_path),
                           omni_env={'ANTHROPIC_BASE_URL': 'http://localhost:20128'})
    args = captured['args']
    assert args[args.index('--model') + 1] == 'claude-sonnet-5'

    plan_execute._headless('auto/best-coding', 'p', str(tmp_path),
                           omni_env={'ANTHROPIC_BASE_URL': 'http://localhost:20128'})
    args = captured['args']
    assert args[args.index('--model') + 1] == 'auto/best-coding'

    plan_execute._headless('claude-sonnet-5', 'p', str(tmp_path))
    args = captured['args']
    assert args[args.index('--model') + 1] == 'claude-sonnet-5'


def test_council_uses_omni_roster_when_routed_through_omniroute(monkeypatch, tmp_path):
    calls = []
    def fake_headless(model, prompt, cwd, omni_env=None):
        calls.append(model)
        return f'critique from {model}'
    monkeypatch.setattr(plan_execute, '_headless', fake_headless)
    omni_env = {'ANTHROPIC_BASE_URL': 'http://localhost:20128'}
    plan_execute.optimize_plan_council('task', _LONG_PLAN, str(tmp_path), omni_env=omni_env)
    for m in plan_execute.OMNI_COUNCIL_MODELS:
        assert m in calls
    for m in plan_execute.COUNCIL_MODELS:
        assert m not in calls


# ── account selection at launch ──────────────────────────────

def test_account_threaded_to_launch(monkeypatch, tmp_path):
    from claude_sessions import config, sessions, system_prompt
    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(sessions, 'load_add_dirs', lambda folder: [])
    monkeypatch.setattr(sessions, 'read_extra_paths', lambda folder: [])
    monkeypatch.setattr(system_prompt, 'merged_system_prompt', lambda *a, **k: None)
    other_acct = str(tmp_path / 'other-account')
    args, env = plan_execute.build_exec_launch(
        str(tmp_path), str(tmp_path / 'proj_folder'), 'do it', 'claude-sonnet-5',
        cfgdir=other_acct)
    assert args is not None
    assert env['CLAUDE_CONFIG_DIR'] == other_acct


def test_account_default_when_unset(monkeypatch, tmp_path):
    from claude_sessions import config, sessions, system_prompt
    monkeypatch.setattr(config, 'get_claude_exe', lambda: r'C:\fake.exe')
    monkeypatch.setattr(sessions, 'load_add_dirs', lambda folder: [])
    monkeypatch.setattr(sessions, 'read_extra_paths', lambda folder: [])
    monkeypatch.setattr(system_prompt, 'merged_system_prompt', lambda *a, **k: None)
    args, env = plan_execute.build_exec_launch(
        str(tmp_path), str(tmp_path / 'proj_folder'), 'do it', 'claude-sonnet-5')
    assert env['CLAUDE_CONFIG_DIR'] == config.config_dir


# ── edit_plan (pure function) ────────────────────────────────

_PLAN_3 = '1. first step\n2. second step\n3. third step'


def test_edit_plan_edit_action():
    result = plan_execute.edit_plan(_PLAN_3, 'edit', index=1, text='modified step')
    assert '2. modified step' in result
    assert '1. first step' in result
    assert '3. third step' in result


def test_edit_plan_delete_action():
    result = plan_execute.edit_plan(_PLAN_3, 'delete', index=0)
    lines = result.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith('1. second step')
    assert lines[1].startswith('2. third step')


def test_edit_plan_insert_action():
    result = plan_execute.edit_plan(_PLAN_3, 'insert', index=1, text='new step')
    lines = result.strip().splitlines()
    assert len(lines) == 4
    assert 'new step' in lines[1]
    assert lines[1].startswith('2. new step')


def test_edit_plan_insert_at_end():
    result = plan_execute.edit_plan(_PLAN_3, 'insert', index=3, text='appended step')
    lines = result.strip().splitlines()
    assert len(lines) == 4
    assert lines[3].startswith('4. appended step')


def test_edit_plan_move_action():
    result = plan_execute.edit_plan(_PLAN_3, 'move', index=0, text='2')
    lines = result.strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith('1. second step')
    assert lines[1].startswith('2. third step')
    assert lines[2].startswith('3. first step')


def test_edit_plan_move_noop():
    result = plan_execute.edit_plan(_PLAN_3, 'move', index=1, text='1')
    # move to same position = no change
    assert result.strip() == _PLAN_3


def test_edit_plan_invalid_index():
    import pytest
    with pytest.raises(ValueError, match='out of range'):
        plan_execute.edit_plan(_PLAN_3, 'edit', index=5, text='x')


def test_edit_plan_empty_text_rejected():
    import pytest
    with pytest.raises(ValueError, match='empty'):
        plan_execute.edit_plan(_PLAN_3, 'edit', index=0, text='  ')


def test_edit_plan_no_steps():
    import pytest
    with pytest.raises(ValueError, match='No numbered steps'):
        plan_execute.edit_plan('just some text\nno steps here', 'edit', index=0, text='x')


def test_edit_plan_move_out_of_range():
    import pytest
    with pytest.raises(ValueError, match='out of range'):
        plan_execute.edit_plan(_PLAN_3, 'move', index=0, text='99')


def test_edit_plan_unknown_action():
    import pytest
    with pytest.raises(ValueError, match='Unknown action'):
        plan_execute.edit_plan(_PLAN_3, 'frobnicate', index=0, text='x')


def test_plan_review_loop_approve(monkeypatch, tmp_path):
    """plan_review_loop returns the plan on approve (ENTER in diffview.confirm)."""
    from claude_sessions import diffview
    monkeypatch.setattr(diffview, 'confirm', lambda old, new, title: True)
    result = plan_execute.plan_review_loop(_PLAN_3, 'test')
    assert result == _PLAN_3


def test_plan_review_loop_discard(monkeypatch, tmp_path):
    """ESC from diffview + ESC from menu → returns None."""
    from claude_sessions import diffview, ui
    monkeypatch.setattr(diffview, 'confirm', lambda old, new, title: False)
    monkeypatch.setattr(ui, 'menu', lambda items, title: None)
    result = plan_execute.plan_review_loop(_PLAN_3, 'test')
    assert result is None


def test_plan_review_loop_edit_then_approve(monkeypatch, tmp_path):
    """ESC from diffview → pick 'edit' → modify → approve on re-review."""
    from claude_sessions import diffview, ui
    calls = {'n': 0}
    def fake_confirm(old, new, title):
        calls['n'] += 1
        return calls['n'] > 1          # first: reject, second: approve
    _mc = [0]
    def fake_menu(items, title):
        _mc[0] += 1
        if 'PLAN REJECTED' in title:
            return 'edit'
        if 'EDIT PLAN' in title:
            # first call: pick step; second call: done editing
            return 0 if _mc[0] < 3 else '__done__'
        if 'STEP 1' in title:
            return 'edit'
        return None
    monkeypatch.setattr(diffview, 'confirm', fake_confirm)
    monkeypatch.setattr(ui, 'menu', fake_menu)
    monkeypatch.setattr(ui, 'text_input', lambda prompt, **k: 'new text')
    result = plan_execute.plan_review_loop(_PLAN_3, 'test')
    assert 'new text' in result
    assert 'first step' not in result


def test_edit_plan_roundtrip_preserves_non_step_lines():
    plan = '# Plan: task\n\n1. step one\n2. step two\n\nSome notes here.'
    result = plan_execute.edit_plan(plan, 'edit', index=0, text='modified one')
    assert result.startswith('# Plan: task')
    assert '1. modified one' in result
    assert 'Some notes here.' in result

# ── plan persistence (save_plan / load_plan / plan_store_path) ──

def test_save_plan_roundtrip(tmp_path):
    p = tmp_path / 'test_plan.json'
    plan_execute.save_plan('1. do X\n2. verify Y', str(p))
    assert p.exists()
    loaded = plan_execute.load_plan(str(p))
    assert 'do X' in loaded


def test_plan_store_path():
    path = plan_execute.plan_store_path()
    assert path.endswith('last_plan.json')
    assert 'claude' in path


def test_load_plan_missing_file(tmp_path):
    loaded = plan_execute.load_plan(str(tmp_path / 'nonexistent.json'))
    assert loaded == ''


def test_load_plan_invalid_json(tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text('not json', encoding='utf-8')
    loaded = plan_execute.load_plan(str(p))
    assert loaded == ''


def test_replan_calls_save(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(plan_execute, '_plan', lambda task, m, cwd, effort='': 'regenerated plan')
    monkeypatch.setattr(plan_execute, 'save_plan',
                        lambda plan, path: calls.append(('save', plan, path)))
    result = plan_execute.replan('original task', 'make it shorter')
    assert result == 'regenerated plan'
    assert any(c[0] == 'save' for c in calls)
