"""The guard that stops claudectl spending an account with nothing left.

Before this, every internal `claude -p` call launched blind: a full 5-hour
window produced a nonzero exit reported as "No output from Claude" while a
second configured account sat there with headroom. Each test below is aimed at
a mutation that would quietly restore that.
"""

import json
import os
import threading

import pytest

from claude_sessions import config as _c
from claude_sessions import events, memory, quota, usage

EXE = os.path.join('C:', 'bin', 'claude.exe')
RESET = '2026-09-01T15:00:00Z'


def _data(*windows):
    """A usage payload. windows: (kind, percent) or (kind, percent, model)."""
    out = []
    for w in windows:
        kind, pct = w[0], w[1]
        item = {'kind': kind, 'percent': pct, 'resets_at': RESET}
        if len(w) > 2:
            item['scope'] = {'model': {'display_name': w[2]}}
        out.append(item)
    return {'limits': out}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    quota._observed.clear()
    quota._decided.clear()
    memory.last_call_error = ''
    monkeypatch.setattr(events, 'path',
                        lambda: str(tmp_path / 'events.jsonl'))
    events._recent.clear()
    monkeypatch.setattr(usage, '_acct_state', {}, raising=False)
    monkeypatch.setattr(_c, 'load_settings', lambda: {'headless_quota': 'prompt'})
    # pytest gives the tests a non-tty stdin, so _interactive() is False unless
    # a test asks for it — but pin it so a terminal-run suite behaves the same.
    monkeypatch.setattr(quota, '_interactive', lambda: False)


def _account(tmp_path, name, pct, kind='session', logged_in=True):
    """An account dir with credentials and a usage cache entry."""
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    if logged_in:
        (d / '.credentials.json').write_text(json.dumps(
            {'claudeAiOauth': {'accessToken': 'tok-' + name,
                               'expiresAt': 9999999999000}}), encoding='utf-8')
    usage._acct_state[str(d)] = {'name': name, 'status': 'ok',
                                 'data': _data((kind, pct))}
    return str(d)


# ── the predicates ───────────────────────────────────────────

def test_a_full_session_window_blocks_and_a_partial_one_does_not(tmp_path):
    d = _account(tmp_path, 'a', 100)
    assert quota.is_exhausted(d)
    assert 'session limit full' in quota.reason(d)
    usage._acct_state[d]['data'] = _data(('session', 99))
    assert not quota.is_exhausted(d)


def test_no_usage_data_fails_open(tmp_path):
    """The single most damaging regression: blocking every internal call on a
    machine where the background poller has never run."""
    assert quota.worst_window(str(tmp_path / 'never-polled')) == (0.0, '', '')
    assert quota.preflight([EXE, '-p', 'hi'], None) == (None, '')

    # present but not yet fetched, and present but failed — both still pass
    for st in ({'status': 'pending'}, {'status': 'error', 'data': None}):
        usage._acct_state[str(tmp_path)] = dict(st, name='x')
        assert not quota.is_exhausted(str(tmp_path))


def test_a_full_per_model_window_does_not_block_the_account(tmp_path):
    """`weekly_scoped` is labelled with the MODEL's name. Refusing a haiku
    extraction because the Opus weekly window is spent is a worse bug than the
    one being fixed."""
    d = str(tmp_path / 'acct')
    usage._acct_state[d] = {'name': 'a', 'status': 'ok', 'data': _data(
        ('session', 10), ('weekly_all', 20), ('weekly_scoped', 100, 'Opus'))}
    assert not quota.is_exhausted(d)


def test_only_claude_print_calls_are_gated(tmp_path):
    """`claude mcp list` and `claude plugin …` share the spawn helpers and cost
    no quota. Gating on args[0] alone would break account management the moment
    any account hit its weekly cap."""
    d = _account(tmp_path, 'a', 100)
    env = _c.account_env(d)
    assert quota.preflight([EXE, 'mcp', 'list'], env)[1] == ''
    assert quota.preflight([EXE, 'plugin', 'marketplace', 'list'], env)[1] == ''
    assert quota.preflight([EXE, '--version'], env)[1] == ''
    assert quota.preflight(['git', 'log', '-p'], env)[1] == ''
    assert quota.preflight([EXE, '-p', 'hi'], env)[1] != ''
    assert quota.preflight([EXE, '--print', 'hi'], env)[1] != ''


def test_a_limit_error_is_recognised_but_a_budget_one_is_not():
    assert quota.is_limit_error('Claude AI usage limit reached')
    assert quota.is_limit_error('HTTP 429 Too Many Requests')
    assert not quota.is_limit_error('exceeded --max-turns limit of 20')
    assert not quota.is_limit_error('')


def test_a_seen_limit_error_stops_the_rest_of_the_burst(tmp_path):
    """The poller refreshes every 300s, so on a cold cache the FIRST call of a
    burst still gets through. That one failure is what stops the other five."""
    d = str(tmp_path / 'acct')
    env = _c.account_env(d)
    assert quota.preflight([EXE, '-p', 'x'], env)[1] == ''      # cache empty
    quota.note_failure(env, 'claude exited 1: Claude AI usage limit reached')
    assert quota.preflight([EXE, '-p', 'x'], env)[1] != ''


# ── the three surfaces ───────────────────────────────────────

def test_unattended_never_prompts_and_records_the_reason(monkeypatch, tmp_path):
    """`ui.menu` is not bridged for job threads — reaching it there is a hang,
    not an error."""
    from claude_sessions import ui
    d = _account(tmp_path, 'a', 100)
    _account(tmp_path, 'b', 5)
    monkeypatch.setattr(usage, '_targets', lambda: [], raising=False)
    monkeypatch.setattr(ui, 'menu', lambda *a, **k: pytest.fail(
        'the guard prompted on a thread that cannot answer'))

    env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(d))
    assert why and 'limit full' in why
    assert env['CLAUDE_CONFIG_DIR'] == d, 'it must not switch without being told'
    assert 'limit full' in memory.last_call_error
    assert any('limit full' in r['msg'] for r in events.read())


def test_auto_switches_to_an_account_with_headroom_and_prompt_does_not(
        monkeypatch, tmp_path):
    a = _account(tmp_path, 'a', 100)
    b = _account(tmp_path, 'b', 5)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a), ('b', b)])

    monkeypatch.setattr(_c, 'load_settings', lambda: {'headless_quota': 'auto'})
    env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert why == ''
    assert env['CLAUDE_CONFIG_DIR'] == b
    # account_env's other half: a key in the environment shadows the login
    assert 'ANTHROPIC_API_KEY' not in env

    quota._decided.clear()
    monkeypatch.setattr(_c, 'load_settings', lambda: {'headless_quota': 'prompt'})
    env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert why, 'prompt must not silently drain the second account'


def test_off_restores_the_old_behaviour(monkeypatch, tmp_path):
    d = _account(tmp_path, 'a', 100)
    monkeypatch.setattr(_c, 'load_settings', lambda: {'headless_quota': 'off'})
    assert quota.preflight([EXE, '-p', 'x'], _c.account_env(d))[1] == ''


def test_a_logged_out_account_is_never_offered(monkeypatch, tmp_path):
    a = _account(tmp_path, 'a', 100)
    b = _account(tmp_path, 'b', 5, logged_in=False)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a), ('b', b)])
    assert quota.headroom(exclude=a) == []


def test_the_tui_offers_the_accounts_with_headroom(monkeypatch, tmp_path):
    from claude_sessions import ui
    a = _account(tmp_path, 'a', 100)
    b = _account(tmp_path, 'b', 5)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a), ('b', b)])
    monkeypatch.setattr(quota, '_interactive', lambda: True)
    seen = {}

    def fake_menu(items, title):
        seen['items'] = items
        seen['title'] = title
        return items[0][1]                      # pick the first account offered
    monkeypatch.setattr(ui, 'menu', fake_menu)

    env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert why == ''
    assert env['CLAUDE_CONFIG_DIR'] == b
    assert 'LIMIT FULL' in seen['title']
    # never None as a value: a None value is a non-selectable separator
    assert all(v is not None for _lbl, v in seen['items'])
    assert [v for _l, v in seen['items']][-2:] == ['__go__', '__cancel__']


def test_cancelling_the_tui_picker_blocks_and_run_anyway_does_not(
        monkeypatch, tmp_path):
    from claude_sessions import ui
    a = _account(tmp_path, 'a', 100)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a)])
    monkeypatch.setattr(quota, '_interactive', lambda: True)

    monkeypatch.setattr(ui, 'menu', lambda *a_, **k: None)          # ESC
    assert quota.preflight([EXE, '-p', 'x'], _c.account_env(a))[1] != ''

    quota._decided.clear()
    monkeypatch.setattr(ui, 'menu', lambda *a_, **k: '__go__')
    assert quota.preflight([EXE, '-p', 'x'], _c.account_env(a))[1] == ''


def test_the_gui_asks_through_the_existing_approval_gate(monkeypatch, tmp_path):
    """Reusing `_gate` is what makes the GUI offer the choice with no frontend
    change at all."""
    from claude_sessions import gui_api
    a = _account(tmp_path, 'a', 100)
    b = _account(tmp_path, 'b', 5)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a), ('b', b)])
    job = gui_api.new_job('test')
    monkeypatch.setattr(quota, '_job', lambda: job)
    gates = []

    def fake_gate(j, title, old, new, diff):
        gates.append((title, diff))
        return True
    monkeypatch.setattr(gui_api, '_gate', fake_gate)

    env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert why == '' and env['CLAUDE_CONFIG_DIR'] == b
    assert 'run under' in gates[0][0]
    # gate['diff'] must be a LIST at every producer — a string is truthy, so
    # the browser's `||[]` fallback never fires and .map() throws.
    assert isinstance(gates[0][1], list)


def test_rejecting_the_gate_blocks_and_tells_the_job_why(monkeypatch, tmp_path):
    from claude_sessions import gui_api
    a = _account(tmp_path, 'a', 100)
    b = _account(tmp_path, 'b', 5)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a), ('b', b)])
    job = gui_api.new_job('test')
    monkeypatch.setattr(quota, '_job', lambda: job)
    monkeypatch.setattr(gui_api, '_gate', lambda *a_, **k: False)

    _env, why = quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert why
    assert job['last_subprocess_error']['output'] == why
    assert job['messages'][-1]['text'] == why


def test_one_answer_covers_a_burst(monkeypatch, tmp_path):
    """A 30-unit memory refresh must ask ONCE. A guard that prompts per call is
    worse than the bug it fixes."""
    from claude_sessions import ui
    a = _account(tmp_path, 'a', 100)
    monkeypatch.setattr(_c, 'all_config_dirs', lambda: [('a', a)])
    monkeypatch.setattr(quota, '_interactive', lambda: True)
    calls = []
    monkeypatch.setattr(ui, 'menu', lambda *a_, **k: (calls.append(1), '__go__')[1])

    for _ in range(5):
        quota.preflight([EXE, '-p', 'x'], _c.account_env(a))
    assert len(calls) == 1


# ── the helpers actually carry the guard ─────────────────────

def test_both_progress_runners_and_the_job_runner_guard_the_spawn():
    """Placement is the whole design: three helpers instead of nine call
    sites. A helper that stops calling preflight silently un-fixes every
    generation feature at once."""
    import inspect
    from claude_sessions import gui_api, ui
    for fn in (ui.run_with_progress, ui.run_with_progress_stdin,
               gui_api._run_cancellable):
        assert 'quota.preflight' in inspect.getsource(fn), fn.__name__


def test_a_failed_headless_call_reports_what_claude_said(monkeypatch, tmp_path):
    """`stderr=DEVNULL` in the two progress runners is what made every failure
    read as "No output from Claude" — including a rate-limited account."""
    import subprocess
    import sys
    from claude_sessions import render, ui

    monkeypatch.setattr(render, 'render_frame', lambda *a, **k: None)
    monkeypatch.setattr(ui, 'flush_input', lambda: None)
    monkeypatch.setattr(ui, 'poll_event', lambda: None)
    # this is the FOREGROUND runner; `_refresh_project` sets silent on whatever
    # thread calls it and never clears it, so an earlier test in the same run
    # otherwise sends this down the headless branch
    monkeypatch.setattr(memory._tls, 'silent', False, raising=False)
    memory.last_call_error = ''

    # not gated: the basename is not claude*
    out, cancelled = ui.run_with_progress(
        [sys.executable, '-c',
         'import sys;sys.stderr.write("Claude AI usage limit reached");sys.exit(1)'],
        ('T',), 'x', timeout=30)
    assert out is None and cancelled is False
    assert 'usage limit' in memory.last_call_error
    assert memory.why_failed() == memory.last_call_error
    assert subprocess  # keep the import meaningful to a reader


def test_the_limit_text_claude_code_actually_sends_is_recognised():
    """The refusal is not phrased like any of the markers that were here.

    Verbatim from a real 429 transcript
    (`~/.claude/projects/D--Claude/56bca8bb-….jsonl`, `"error":"rate_limit"`,
    `"apiErrorStatus":429`): the assistant message is the sentence below. It
    matched NOTHING, so `note_failure` never latched, and the "one failure stops
    the other five" guard had never fired for the commonest rejection.
    """
    from claude_sessions import quota
    assert quota.is_limit_error(
        "You've hit your session limit · resets 2:30am (Europe/Rome)")
    assert quota.is_limit_error("You've hit your weekly limit · resets Monday")
    # and the shapes that already worked still do
    assert quota.is_limit_error('Claude AI usage limit reached')
    assert quota.is_limit_error('429 Too Many Requests')
    # a bare 'limit' must still NOT match — the budget cap and the turn cap say
    # that, and neither is out-of-quota
    assert not quota.is_limit_error('--max-turns limit of 20 turns')
    assert not quota.is_limit_error('') and not quota.is_limit_error(None)


def test_the_markers_do_not_match_the_models_own_output():
    """`ui._note_failure` hands `note_failure` the whole STDOUT now, not a
    300-char stderr latch — so every marker is scanned against text the model
    wrote. A bare '429' substring hit a token count and a bare 'quota' hit any
    answer that mentioned rate limiting, latching the account out of headless
    work for fifteen minutes after a run that had nothing wrong with it.
    """
    from claude_sessions import quota
    for benign in ('total tokens: 14290', 'cost 4291 tokens',
                   'wrote 429 lines to quota.py',
                   'reviewed quota.py; the quota system looks fine',
                   'the daily quota display needs a label'):
        assert not quota.is_limit_error(benign), benign
    # the real one is still recognised, boundary and all
    assert quota.is_limit_error('HTTP 429 Too Many Requests')
    assert quota.is_limit_error('your quota exceeded for this window')


def test_a_json_envelope_failure_reports_the_sentence_not_the_blob():
    """`--output-format json` buries the reason behind ~200 chars of metadata,
    and every reporter truncates — so what reached the user was a clipped blob
    with the sentence cut off. Twenty of those are in the live event log."""
    from claude_sessions.gui_api import _claude_failure_reason
    blob = json.dumps({
        'duration_api_ms': 0, 'stop_reason': 'stop_sequence',
        'session_id': '56bca8bb-6932-4cf8-be99-e7a793e01475',
        'total_cost_usd': 0, 'usage': {'input_tokens': 0, 'output_tokens': 0},
        'is_error': True,
        'result': "You've hit your session limit · resets 2:30am (Europe/Rome)",
    })
    assert len(blob) > 200                        # the truncation that hid it
    assert _claude_failure_reason(blob) == \
        "You've hit your session limit · resets 2:30am (Europe/Rome)"
    # nested error objects, and the plain-text paths, both survive unchanged
    assert _claude_failure_reason(
        json.dumps({'error': {'message': 'overloaded'}})) == 'overloaded'
    assert _claude_failure_reason('Traceback (most recent call last):') == \
        'Traceback (most recent call last):'
    assert _claude_failure_reason('{not json at all') == '{not json at all'
    assert _claude_failure_reason('') == '' and _claude_failure_reason(None) == ''
