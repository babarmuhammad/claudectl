import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from harness import (Sandbox, run_flow, typed,
                     UP, DOWN, LEFT, RIGHT, ENTER, ESC)

from claude_sessions import main as main_mod


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def run_main(monkeypatch, sb, keys):
    result, cap, exhausted = run_flow(monkeypatch, keys, _run_catch_exit)
    return cap, exhausted


def _run_catch_exit():
    try:
        main_mod.run()
        return 'returned'
    except SystemExit as e:
        if str(e) == 'OUT_OF_KEYS':
            raise
        return f'exit:{e.code}'


def test_smoke_main_menu_renders(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha')
    cap = run_flow(monkeypatch, flat(ESC), _run_catch_exit)[1]
    plain = cap.plain
    assert 'SELECT PROJECT' in plain
    assert 'type to search' in plain          # hint bar visible
    assert 'alpha' in plain
    assert 'daily' in plain                   # usage banner
    assert 'TestMCP' in plain                 # mcp footer


def test_select_project_then_session_writes_choice(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha', n_sessions=1)
    # ENTER project -> sessions -> down past New Chat? first nav row is New Chat
    keys = flat(ENTER,            # select project (first row)
                ENTER,            # 'new' (first selectable)
                ENTER)            # launch with defaults
    cap, _ = run_main(monkeypatch, sb, keys)
    line = sb.choice_line()
    assert line is not None
    parts = line.split('|')
    assert parts[0] == 'v5'
    assert parts[3] == 'new'


def test_quickresume_esc_returns_to_main(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=1)
    sb.write_last_sessions([{
        'project_path': actual, 'encoded_name': enc,
        'session_id': sids[0], 'preview': 'recent work', 'timestamp': time.time(),
    }])
    # ENTER on quick-resume -> launch options -> ESC -> back at main -> ESC exit
    keys = flat(ENTER, ESC, ESC)
    cap, _ = run_main(monkeypatch, sb, keys)
    assert sb.choice_line() is None           # nothing launched
    assert 'LAUNCH OPTIONS' in cap.plain


def test_quickresume_enter_launches_resume(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    actual, enc, folder, sids = sb.add_project('alpha', n_sessions=1)
    sb.write_last_sessions([{
        'project_path': actual, 'encoded_name': enc,
        'session_id': sids[0], 'preview': 'recent work', 'timestamp': time.time(),
    }])
    keys = flat(ENTER, ENTER)
    run_main(monkeypatch, sb, keys)
    line = sb.choice_line()
    assert line and f'resume:{sids[0]}' in line


def test_type_to_filter_projects(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha')
    sb.add_project('beta')
    # type 'beta' -> ENTER selects filtered project -> sessions ESC -> main ESC
    keys = flat(typed('beta'), ENTER, ESC, ESC)
    cap, _ = run_main(monkeypatch, sb, keys)
    assert 'SESSIONS' in cap.plain
    # the sessions screen shown must be beta's
    assert 'beta' in cap.plain


def test_help_screen_roundtrip(monkeypatch, tmp_path):
    # NOTE: don't navigate by typing 'help' — tmp_path contains the test
    # name, so the project label would match the filter too.
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha')
    # UP wraps to the last item ('?  Help')
    keys = flat(UP, ENTER, ENTER, ESC)
    cap, _ = run_main(monkeypatch, sb, keys)
    assert 'HELP' in cap.plain
    assert 'Sessions screen' in cap.plain


def test_settings_screen_roundtrip(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha')
    # UP x2 wraps to second-from-last item ('⚙  Settings')
    keys = flat(UP, UP, ENTER, ESC, ESC)
    cap, _ = run_main(monkeypatch, sb, keys)
    assert 'SETTINGS' in cap.plain
    assert 'Editor' in cap.plain


def test_small_terminal_hint_visible_with_markers(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path, terminal=(90, 18))
    for i in range(15):
        sb.add_project(f'proj{i:02d}', n_sessions=0)
    cap = run_flow(monkeypatch, flat(ESC), _run_catch_exit)[1]
    plain = cap.plain
    assert 'type to search' in plain          # hint bar still on screen
    assert 'more' in plain                    # overflow marker shown


def test_nav_windowing_follows_selection(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path, terminal=(90, 16))
    for i in range(20):
        sb.add_project(f'proj{i:02d}', n_sessions=0)
    # navigate down 15 times, then ESC — selected row must be visible
    keys = flat(*([DOWN] * 15), ESC)
    cap, _ = run_main(monkeypatch, sb, keys)
    assert 'proj14' in cap.plain or 'proj15' in cap.plain


def test_esc_clears_filter_before_exit(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path)
    sb.add_project('alpha')
    # type filter, ESC clears it (menu stays), second ESC exits
    keys = flat(typed('zzz_nomatch'), ESC, ESC)
    cap, exhausted = run_main(monkeypatch, sb, keys)
    assert not exhausted                       # flow ended by itself (exit)
