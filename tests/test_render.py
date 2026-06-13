import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_sessions.render import (strip_ansi, disp_width, trunc, pad, fit,
                                     cols, meter, progress_bar, sep_line,
                                     hline, header, content_width, hint_keys)
from claude_sessions import render
from claude_sessions import config as _c


def test_strip_ansi():
    assert strip_ansi('\x1b[96mhello\x1b[0m') == 'hello'
    assert strip_ansi('plain') == 'plain'
    assert strip_ansi('\x1b[38;5;117mx\x1b[48;2;1;2;3my') == 'xy'


def test_disp_width_ascii():
    assert disp_width('hello') == 5


def test_disp_width_ignores_ansi():
    assert disp_width('\x1b[96mhello\x1b[0m') == 5


def test_disp_width_wide_chars():
    assert disp_width('中') == 2
    assert disp_width('日本語') == 6
    assert disp_width('a中b') == 4


def test_disp_width_ambiguous_narrow():
    # ★ ☆ are East Asian 'A' (ambiguous) — treated as 1 col (Windows Terminal default)
    assert disp_width('★') == 1


def test_trunc_no_cut():
    assert trunc('short', 10) == 'short'


def test_trunc_cuts_with_ellipsis():
    out = trunc('abcdefghij', 5)
    assert out.endswith('…')
    assert disp_width(out) <= 5


def test_trunc_ansi_safe():
    s = '\x1b[96mabcdefghij\x1b[0m'
    out = trunc(s, 5)
    assert disp_width(out) <= 5
    assert out.endswith('\x1b[0m')          # reset appended
    assert '\x1b[96m' in out                # code preserved


def test_trunc_wide_chars():
    out = trunc('中中中中', 5)
    assert disp_width(out) <= 5


def test_pad_left_right():
    assert pad('ab', 5) == 'ab   '
    assert pad('ab', 5, align='right') == '   ab'
    assert pad('abcdef', 3) == 'abcdef'   # never truncates


def test_pad_accounts_for_ansi():
    s = '\x1b[96mab\x1b[0m'
    assert disp_width(pad(s, 5)) == 5


def test_fit_exact_width():
    assert disp_width(fit('hello world this is long', 10)) == 10
    assert disp_width(fit('hi', 10)) == 10


def test_cols_total_width(monkeypatch):
    monkeypatch.setattr(render, 'content_width', lambda: 60)
    line = cols(['a', 'b', 'c'], [5, 7, None])
    assert disp_width(line) <= 60
    assert disp_width(line) >= 50


def test_render_frame_no_console_fallback(capsys):
    # No tty → fallback path; must not raise
    render.invalidate()
    render.render_frame(['line one', 'line two'])


def test_screen_init_noop_without_tty():
    render.screen_init()
    assert render.screen_active() is False   # no tty in test harness
    render.screen_restore()                  # idempotent, no raise


# ── frame diff + width edges (suite H) ───────────────────────

class _VTCapture:
    def __init__(self):
        self.buf = []

    def write(self, t):
        self.buf.append(t)

    def flush(self):
        pass

    def isatty(self):
        return True


def _vt_render(monkeypatch, frames, cols=100, lines=40):
    """Render frames through the VT diff path; return list of emitted strings."""
    import sys as _sys
    monkeypatch.setattr(render, '_vt_ok', lambda: True)
    size = os.terminal_size((cols, lines))
    monkeypatch.setattr(render.shutil, 'get_terminal_size', lambda *a, **k: size)
    cap = _VTCapture()
    monkeypatch.setattr(_sys, 'stdout', cap)
    render.invalidate()
    render._last_size = size
    out = []
    for fr in frames:
        cap.buf.clear()
        render.render_frame(fr)
        out.append(''.join(cap.buf))
    monkeypatch.setattr(_sys, 'stdout', _sys.__stdout__)
    return out


def test_frame_shrink_clears_dropped_lines(monkeypatch):
    # frame goes from 5 lines to 2 — rows 3,4,5 must be cleared (erased)
    big = [f'row {i}' for i in range(5)]
    small = ['row 0', 'row 1']
    outs = _vt_render(monkeypatch, [big, small])
    second = outs[1]
    # cursor moves to rows 3/4/5 with erase-line (\x1b[K) and empty content
    assert '\x1b[3;1H\x1b[K' in second
    assert '\x1b[5;1H\x1b[K' in second


def test_frame_diff_only_changed_lines(monkeypatch):
    f1 = ['a', 'b', 'c']
    f2 = ['a', 'X', 'c']    # only row 2 changed
    outs = _vt_render(monkeypatch, [f1, f2])
    second = outs[1]
    assert '\x1b[2;1H\x1b[KX' in second
    assert '\x1b[1;1H' not in second   # unchanged row 1 not rewritten


def test_content_width_floor_and_uncapped(monkeypatch):
    monkeypatch.setattr(render.shutil, 'get_terminal_size',
                        lambda *a, **k: os.terminal_size((20, 10)))
    assert content_width() == 40        # floor
    monkeypatch.setattr(render.shutil, 'get_terminal_size',
                        lambda *a, **k: os.terminal_size((200, 50)))
    assert content_width() == 199       # uncapped


def test_fit_never_wraps_long_line():
    s = 'x' * 500
    out = fit(s, 50)
    assert disp_width(out) == 50
    assert '\n' not in out


def test_meter_clamps():
    assert strip_ansi(meter(0)) == '▕░░░░░░░░░░▏'
    assert strip_ansi(meter(100)) == '▕██████████▏'
    assert strip_ansi(meter(250)) == '▕██████████▏'   # clamp >100
    assert strip_ansi(meter(-10)) == '▕░░░░░░░░░░▏'    # clamp <0


def test_hint_keys_colors_and_fits(monkeypatch):
    monkeypatch.setattr(render, 'content_width', lambda: 80)
    line = hint_keys([('g', 'agents'), ('?', 'help')])
    assert _c.C_ACCENT in line          # keys accent-colored
    assert 'agents' in strip_ansi(line) and 'help' in strip_ansi(line)
    assert disp_width(line) <= 80


def test_progress_bar_width(monkeypatch):
    for tick in (0, 7, 50):
        assert len(strip_ansi(progress_bar(tick, width=36))) == 36


def test_sep_line_stretches(monkeypatch):
    monkeypatch.setattr(render, 'content_width', lambda: 80)
    line = sep_line('─' * 62)
    assert disp_width(strip_ansi(line)) >= 70    # stretched to width


def test_sep_line_label_passthrough(monkeypatch):
    monkeypatch.setattr(render, 'content_width', lambda: 80)
    assert 'no sessions' in strip_ansi(sep_line('(no sessions)'))


def test_header_many_crumbs_fits(monkeypatch):
    monkeypatch.setattr(render, 'content_width', lambda: 60)
    h = header('CLAUDECTL', 'verylongprojectname', 'SESSIONS', 'EXTRA', 'MORE')
    assert disp_width(strip_ansi(h)) <= 60
