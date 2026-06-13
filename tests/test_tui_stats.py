import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, ENTER, ESC

from claude_sessions import stats as stats_mod
from claude_sessions import usage as usage_mod
from claude_sessions.search import global_search
from claude_sessions.render import strip_ansi


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def entries_for(sb, projects):
    out = []
    for actual, enc in projects:
        out.append((0, actual, enc))
    return out


def test_dashboard_renders_and_drills(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, _ = sb.add_project('alpha', n_sessions=2)
    a2, e2, f2, _ = sb.add_project('beta', n_sessions=1)
    keys = flat(ENTER, ESC, ESC)              # drill into top project, back, exit
    result, cap, _ = run_flow(monkeypatch, keys, stats_mod.usage_dashboard,
                              entries_for(sb, [(a1, e1), (a2, e2)]))
    plain = cap.plain
    assert 'USAGE STATS' in plain
    assert 'alpha' in plain and 'beta' in plain
    assert 'est.$' in plain
    assert 'USAGE' in plain                   # drill-down screen header crumb


def test_dashboard_esc_shows_partial(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, _ = sb.add_project('alpha', n_sessions=2)
    keys = flat(ESC, ESC)        # cancel scan, then leave dashboard
    result, cap, _ = run_flow(monkeypatch, keys, stats_mod.usage_dashboard,
                              entries_for(sb, [(a1, e1)]))
    assert '(partial)' in cap.plain


def test_abandoned_scan_generator_still_saves_cache(monkeypatch, tmp_path):
    """Regression for audit #6: early-abandoned generator must persist cache."""
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, _ = sb.add_project('alpha', n_sessions=3)
    gen = stats_mod.iter_all_sessions(entries_for(sb, [(a1, e1)]))
    next(gen)            # parse one session
    gen.close()          # abandon mid-scan
    assert os.path.exists(stats_mod.cache_file)
    data = json.load(open(stats_mod.cache_file, encoding='utf-8'))
    assert len(data) >= 1


def test_dashboard_empty(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    result, cap, _ = run_flow(monkeypatch, flat(ESC),
                              stats_mod.usage_dashboard, [])
    assert 'USAGE STATS' in cap.plain         # renders without crash


def test_project_usage_screen(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=2)
    result, cap, _ = run_flow(monkeypatch, flat(DOWN, ESC),
                              stats_mod.project_usage_screen, folder, 'alpha')
    plain = cap.plain
    assert 'sess' in plain or 'session' in plain
    assert 'Session 0' in plain               # titled session listed


def test_global_search_finds_and_returns(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, sids1 = sb.add_project('alpha', n_sessions=1, title='Fix the login bug')
    a2, e2, f2, sids2 = sb.add_project('beta', n_sessions=1, title='Write docs')
    keys = flat(typed('login'), ENTER)
    result, cap, _ = run_flow(monkeypatch, keys, global_search,
                              entries_for(sb, [(a1, e1), (a2, e2)]))
    assert result is not None
    kind, path, enc, sid = result
    assert kind == 'resume' and enc == e1 and sid == sids1[0]


def test_global_search_multiword_and(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, _ = sb.add_project('alpha', n_sessions=1, title='red green blue')
    a2, e2, f2, _ = sb.add_project('beta', n_sessions=1, title='red yellow')
    keys = flat(typed('red blue'), ESC, ESC)   # AND: only alpha matches
    result, cap, _ = run_flow(monkeypatch, keys, global_search,
                              entries_for(sb, [(a1, e1), (a2, e2)]))
    assert '1 match' in cap.plain


def test_global_search_esc_cancels(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    a1, e1, f1, _ = sb.add_project('alpha', n_sessions=1)
    result, cap, _ = run_flow(monkeypatch, flat(ESC), global_search,
                              entries_for(sb, [(a1, e1)]))
    assert result is None


# ── usage banner parsing edge cases ──────────────────────────

def test_usage_pct_clamped_above_100(monkeypatch, tmp_path):
    w = usage_mod._extract_windows({'five_hour': {'utilization': 250,
                                                  'resets_at': None}})
    assert w and 0 <= w[0][1] <= 100


def test_usage_pct_zero_shown(monkeypatch, tmp_path):
    w = usage_mod._extract_windows({'five_hour': {'utilization': 0,
                                                  'resets_at': None}})
    assert w and w[0][1] == 0


def test_usage_pct_fraction_scaled(monkeypatch, tmp_path):
    w = usage_mod._extract_windows({'five_hour': {'utilization': 0.42,
                                                  'resets_at': None}})
    assert w and abs(w[0][1] - 42) < 1e-6


def test_usage_negative_rejected(monkeypatch, tmp_path):
    w = usage_mod._extract_windows({'five_hour': {'utilization': -5,
                                                  'resets_at': None}})
    assert not w or w[0][1] == 0


def test_usage_no_data_empty_line(monkeypatch, tmp_path):
    monkeypatch.setattr(usage_mod, '_started', True)
    monkeypatch.setattr(usage_mod, '_ready', True)
    monkeypatch.setattr(usage_mod, '_data', None)
    assert usage_mod.usage_status_line() == ''


def test_usage_label_mapping(monkeypatch, tmp_path):
    assert usage_mod._window_label('five_hour') == 'daily'
    assert usage_mod._window_label('seven_day') == 'weekly'
    assert usage_mod._window_label('seven_day_opus') == 'wk-opus'
