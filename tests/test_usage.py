import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions.usage import _extract_windows


# Real OAuth /usage response shape (trimmed): utilization is 0-100, many null
# siblings, plus an authoritative `limits` array and dict siblings.
REAL = {
    'five_hour': {'utilization': 18.0, 'resets_at': '2026-06-24T14:00:00Z'},
    'seven_day': {'utilization': 23.0, 'resets_at': '2026-06-28T07:00:00Z'},
    'seven_day_opus': None,
    'seven_day_sonnet': None,
    'extra_usage': {'is_enabled': False, 'utilization': None},
    'limits': [
        {'kind': 'session', 'group': 'session', 'percent': 18,
         'resets_at': '2026-06-24T14:00:00Z', 'is_active': False},
        {'kind': 'weekly_all', 'group': 'weekly', 'percent': 23,
         'resets_at': '2026-06-28T07:00:00Z', 'is_active': True},
    ],
    'spend': {'percent': 0, 'enabled': False},
}


def _as_dict(windows):
    return {label: pct for label, pct, _ in windows}


def test_real_shape_uses_limits_array():
    w = _as_dict(_extract_windows(REAL))
    assert w == {'session': 18.0, 'weekly': 23.0}


def test_low_weekly_not_inflated_to_100_via_limits():
    data = {'limits': [{'group': 'weekly', 'percent': 1, 'resets_at': None}]}
    assert _as_dict(_extract_windows(data)) == {'weekly': 1.0}


def test_fallback_dict_windows_0_to_100():
    data = {'five_hour': {'utilization': 30}, 'seven_day': {'utilization': 55}}
    assert _as_dict(_extract_windows(data)) == {'session': 30.0, 'weekly': 55.0}


def test_multi_account_bars(monkeypatch):
    import claude_sessions.usage as u
    win = {'limits': [{'kind': 'session', 'group': 'session', 'percent': 11,
                       'resets_at': None}]}
    monkeypatch.setattr(u, '_started', True)
    monkeypatch.setattr(u, '_ready', True)
    monkeypatch.setattr(u, '_data', win)
    monkeypatch.setattr(u, '_acct_state', {
        r'C:\.claude': {'name': 'default', 'email': 'me@a.com', 'data': win},
        r'C:\.claude-work': {'name': 'work', 'email': 'work@b.com', 'data': win}})
    line = u.usage_status_line()
    assert '\n' in line                              # one bar per account
    assert 'me@a.com' in line and 'work@b.com' in line
    assert line.count('session') == 2


def test_single_account_no_label(monkeypatch):
    import claude_sessions.usage as u
    win = {'limits': [{'kind': 'session', 'group': 'session', 'percent': 5, 'resets_at': None}]}
    monkeypatch.setattr(u, '_started', True)
    monkeypatch.setattr(u, '_ready', True)
    monkeypatch.setattr(u, '_data', win)
    monkeypatch.setattr(u, '_acct_state', {})        # only default, no extras
    line = u.usage_status_line()
    assert '\n' not in line and 'session' in line    # single compact bar


def test_targets_include_configured_accounts(monkeypatch, tmp_path):
    import claude_sessions.usage as u
    monkeypatch.setattr('claude_sessions.config.load_settings',
                        lambda: {'accounts': [{'name': 'work', 'dir': r'C:\work'}]})
    names = [n for n, _d in u._targets()]
    assert 'default' in names and 'work' in names


def test_regression_fractionlike_value_not_multiplied():
    # The old `0 < pct <= 1.0: pct *= 100` heuristic turned 1% into 100%.
    data = {'seven_day': {'utilization': 1.0}}
    assert _as_dict(_extract_windows(data)) == {'weekly': 1.0}


def test_clamped_and_skips_nulls():
    data = {'seven_day': {'utilization': 250}, 'seven_day_opus': None,
            'junk': [1, 2, 3]}
    assert _as_dict(_extract_windows(data)) == {'weekly': 100.0}


def test_empty_and_nondict():
    assert _extract_windows(None) == []
    assert _extract_windows({}) == []
    assert _extract_windows({'limits': []}) == []
