"""Regression tests locking in audit-driven fixes."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, ENTER, ESC, BACK

from claude_sessions import main as main_mod, usage as usage_mod
from claude_sessions.session_menu import sessions_menu
from claude_sessions.sessions import scan_sessions, save_name


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


# ── #1: name cache keyed by (folder, sid) — no live/archived collision ──

def test_name_cache_keyed_by_folder(monkeypatch, tmp_path):
    # A live session and an archived session sharing the same sid must show
    # their own on-disk names. Within ONE menu session the names cache is
    # keyed (folder, sid); a bare-sid key would cross-contaminate.
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=1)
    sid = sids[0]
    save_name(folder, sid, 'LIVE NAME')
    # plant an archived session with the same sid but a different name
    arch = os.path.join(folder, 'archived')
    os.makedirs(arch, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(folder, f'{sid}.jsonl'),
                os.path.join(arch, f'{sid}.jsonl'))
    save_name(arch, sid, 'ARCHIVED NAME')
    # one menu: see live (LIVE NAME), toggle to archived (ARCHIVED NAME)
    keys = flat(typed('A'), ESC, ESC)
    _, cap, _ = run_flow(monkeypatch, keys, sessions_menu,
                         scan_sessions(folder), folder, 'alpha', actual)
    plain = cap.plain
    assert 'LIVE NAME' in plain        # live view (before toggle)
    assert 'ARCHIVED NAME' in plain    # archived view (after A) — own name


# ── #9: '|' stripped from name/worktree, not aborted ──

def test_pipe_in_name_stripped(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    _, enc, folder, _ = sb.add_project('alpha', n_sessions=0)
    captured = {}

    def fake_launch_opts(name, defaults=None, is_new=False):
        return {'effort': '', 'model': '', 'perm': '',
                'name': 'a|b|c', 'worktree': 'w|t'}
    monkeypatch.setattr(main_mod, 'launch_options_menu', fake_launch_opts)
    # ENTER project -> ENTER New Chat -> launch (opts faked)
    keys = flat(ENTER, ENTER)

    def fn():
        try:
            main_mod.run()
        except SystemExit as e:
            if str(e) == 'OUT_OF_KEYS':
                raise
    run_flow(monkeypatch, keys, fn)
    line = sb.choice_line()
    assert line is not None
    parts = line.split('|')
    # v3|path|enc|action|effort|model|perm|name|worktree|cfg  -> 10 fields
    assert len(parts) == 10
    assert parts[7] == 'abc'      # name pipes stripped
    assert parts[8] == 'wt'       # worktree pipes stripped


# ── #7: project with no matching entry skipped safely (no crash) ──

def test_unmatched_path_no_crash(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha', n_sessions=1)
    # normal flow still works (guard doesn't break happy path)
    keys = flat(ENTER, ENTER, ENTER)

    def fn():
        try:
            main_mod.run()
        except SystemExit as e:
            if str(e) == 'OUT_OF_KEYS':
                raise
    run_flow(monkeypatch, keys, fn)
    assert sb.choice_line() is not None


# ── #4/#7/#19: usage pct clamping & zero ──

def test_usage_clamp_and_zero():
    assert usage_mod._extract_windows(
        {'five_hour': {'utilization': 250, 'resets_at': None}})[0][1] == 100
    assert usage_mod._extract_windows(
        {'five_hour': {'utilization': 0, 'resets_at': None}})[0][1] == 0
    assert not usage_mod._extract_windows(
        {'five_hour': {'utilization': -1, 'resets_at': None}})


# ── #6 windowing: full session menu hint always present, small terminal ──

def test_sessions_two_hint_lines_fit_small_terminal(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path, terminal=(90, 14))
    actual, enc, folder, _ = sb.add_project('alpha', n_sessions=20)
    _, cap, _ = run_flow(monkeypatch, flat(ESC), sessions_menu,
                         scan_sessions(folder), folder, 'alpha', actual)
    # both hint lines render even in a 14-row window
    assert 'r rename' in cap.plain
    assert 'A archived' in cap.plain
    assert 'more' in cap.plain
