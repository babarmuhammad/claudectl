import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox, run_flow, typed, UP, DOWN, LEFT, RIGHT, ENTER, ESC

from claude_sessions.ui import pager
from claude_sessions.claude_md import _pager_confirm


def flat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


LINES = [f'line {i:03d} content' for i in range(100)]


def run_pager(monkeypatch, keys, lines=None, **kw):
    return run_flow(monkeypatch, keys, pager, ('T',), lines or LINES, **kw)


def test_scroll_clamps_at_top(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, cap, _ = run_pager(monkeypatch, flat(UP, UP, UP, ESC))
    assert 'line 000' in cap.plain


def test_page_and_scroll_to_end(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(*([RIGHT] * 10), ESC)         # page far past end, clamps
    result, cap, _ = run_pager(monkeypatch, keys)
    assert 'line 099' in cap.plain


def test_search_jumps_and_wraps(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    lines = list(LINES)
    lines[40] = 'needle alpha'
    lines[80] = 'needle beta'
    keys = flat(typed('/'), typed('needle'), ENTER,   # jump to 40
                typed('n'),                            # -> 80
                typed('n'),                            # wraps -> 40
                typed('p'),                            # back -> 80
                ESC,                                   # clear search
                ESC)                                   # exit
    result, cap, _ = run_pager(monkeypatch, keys, lines=lines)
    assert "'needle'" in cap.plain            # search status shown
    assert 'needle beta' in cap.plain


def test_search_no_match_flashes(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    keys = flat(typed('/'), typed('zzznope'), ENTER, ESC)
    result, cap, _ = run_pager(monkeypatch, keys)
    assert 'No matches' in cap.plain


def test_marks_counter_boundaries(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    marks = [0, 30, 60, 90]
    # at top -> msg 1/4
    result, cap, _ = run_pager(monkeypatch, flat(ESC), marks=marks)
    assert 'msg 1/4' in cap.plain
    # scroll to bottom: top clamps to line 71 (100 - page), which is inside
    # message 3 (starts at 60) — counter reflects the message at the TOP of
    # the view, so 3/4 is correct here
    keys = flat(*([RIGHT] * 8), ESC)
    result, cap, _ = run_pager(monkeypatch, keys, marks=marks)
    assert 'msg 3/4' in cap.plain


def test_extra_keys_returned(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, cap, _ = run_pager(monkeypatch, flat(typed('i')), extra_keys=('i', 'e'))
    assert result == 'i'


def test_enter_exits(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, cap, ex = run_pager(monkeypatch, flat(ENTER))
    assert result is None and not ex


def test_resize_redraws_with_new_width(monkeypatch, tmp_path):
    sb = Sandbox(monkeypatch, tmp_path, terminal=(60, 20))
    # harness can't change size mid-flow easily; verify small width renders
    result, cap, _ = run_pager(monkeypatch, flat(ESC))
    assert 'line 000' in cap.plain


def test_pager_confirm_approve(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    content = '\n'.join(f'row {i}' for i in range(60))
    result, cap, _ = run_flow(monkeypatch, flat(DOWN, DOWN, ENTER),
                              _pager_confirm, 'TEST', content)
    assert result is True
    assert 'REVIEW' in cap.plain


def test_pager_confirm_reject(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    result, cap, _ = run_flow(monkeypatch, flat(ESC),
                              _pager_confirm, 'TEST', 'one\ntwo')
    assert result is False


def test_pager_confirm_scroll_end_marker(monkeypatch, tmp_path):
    Sandbox(monkeypatch, tmp_path)
    content = '\n'.join(f'row {i}' for i in range(10))
    result, cap, _ = run_flow(monkeypatch, flat(ENTER),
                              _pager_confirm, 'TEST', content)
    assert '(end)' in cap.plain
    assert result is True
