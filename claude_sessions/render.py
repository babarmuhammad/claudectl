"""Frame-based rendering for the claudectl TUI.

Screens build a list[str] frame; render_frame() diffs it against the
previous frame and rewrites only changed lines (wrapped in DECSET 2026
synchronized output) — flicker-free on Windows Terminal. Falls back to
clear+reprint when VT/console is unavailable (legacy conhost, tests).
"""

import os
import re
import sys
import shutil
import unicodedata

from . import config as _c
from .config import C_RESET, C_DIM, C_BOLD


# ── display width (ANSI- and wide-glyph-aware) ───────────────

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def strip_ansi(s):
    return _ANSI_RE.sub('', s)


def disp_width(s):
    """Terminal display width: ANSI codes 0, wide/fullwidth 2, combining 0."""
    width = 0
    for ch in strip_ansi(s):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return width


def trunc(s, width):
    """Truncate to display width, appending '…' if cut. ANSI-safe:
    escape codes are preserved and never split; C_RESET appended if any code present."""
    if disp_width(s) <= width:
        return s
    out = []
    used = 0
    limit = max(0, width - 1)   # room for ellipsis
    i = 0
    had_ansi = False
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            had_ansi = True
            i = m.end()
            continue
        ch = s[i]
        w = 0 if unicodedata.combining(ch) else \
            (2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1)
        if used + w > limit:
            break
        out.append(ch)
        used += w
        i += 1
    out.append('…')
    if had_ansi:
        out.append(C_RESET)
    return ''.join(out)


def pad(s, width, align='left'):
    """Pad to display width (never truncates)."""
    gap = width - disp_width(s)
    if gap <= 0:
        return s
    return (' ' * gap + s) if align == 'right' else (s + ' ' * gap)


def fit(s, width, align='left'):
    """Truncate + pad to exactly `width` display columns."""
    return pad(trunc(s, width), width, align)


# ── frame renderer ───────────────────────────────────────────

_prev_frame    = None
_screen_active = False
_last_size     = None


def _vt_ok():
    """VT output usable right now (real console + VT mode enabled)?"""
    from . import ui
    if not ui._VT_ENABLED:
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _w(text):
    """Write to stdout, tolerating closed/odd streams (test harness)."""
    try:
        sys.stdout.write(text)
    except Exception:
        _c.log.exception('render write failed')


def _flush():
    try:
        sys.stdout.flush()
    except Exception:
        _c.log.exception('render flush failed')


def content_width():
    """Usable frame width — full terminal width (min 40)."""
    try:
        cols = shutil.get_terminal_size().columns
    except Exception:
        cols = 80
    return max(cols - 1, 40)


def invalidate():
    """Force a full repaint on the next render_frame call."""
    global _prev_frame
    _prev_frame = None


def render_frame(lines):
    """Render a frame. Diff-rewrite on VT consoles; clear+print fallback otherwise."""
    global _prev_frame, _last_size

    if not _vt_ok():
        # Legacy behavior: clear + full print
        from .ui import _cls
        _cls()
        _w('\n'.join(lines) + '\n')
        _flush()
        _prev_frame = None
        return

    try:
        size = shutil.get_terminal_size()
    except Exception:
        size = None
    if size != _last_size:
        _last_size = size
        invalidate()

    prev = _prev_frame
    out = ['\x1b[?2026h']   # begin synchronized update (ignored if unsupported)

    if prev is None:
        out.append('\x1b[2J\x1b[H')
        for i, line in enumerate(lines):
            out.append(f'\x1b[{i+1};1H\x1b[K{line}')
    else:
        n = max(len(lines), len(prev))
        for i in range(n):
            new = lines[i] if i < len(lines) else ''
            old = prev[i] if i < len(prev) else None
            if new != old:
                out.append(f'\x1b[{i+1};1H\x1b[K{new}')

    out.append('\x1b[?2026l')
    _w(''.join(out))
    _flush()
    _prev_frame = list(lines)


def screen_init():
    """Enter alternate screen buffer + hide cursor. Idempotent; no-op without VT."""
    global _screen_active
    if _screen_active or not _vt_ok():
        return
    _w('\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H')
    _flush()
    _screen_active = True
    invalidate()


def screen_restore():
    """Leave alternate buffer + restore cursor. Idempotent, safe to call twice."""
    global _screen_active
    if not _screen_active:
        return
    _w('\x1b[0m\x1b[?25h\x1b[?1049l')
    _flush()
    _screen_active = False
    invalidate()


def screen_active():
    return _screen_active


def frame_height():
    """Rows available in the current console window."""
    try:
        return shutil.get_terminal_size().lines
    except Exception:
        return 24


# ── frame line builders ──────────────────────────────────────

def header(*crumbs):
    """Breadcrumb title bar:  CLAUDECTL ▸ project ▸ SESSIONS """
    hb = _c.C_HEADER_BG
    text = '  ' + f' ▸ '.join(f'{C_BOLD}{c}{C_RESET}{hb}' for c in crumbs) + ' '
    w = content_width()
    return f'{hb}{fit(text, w)}{C_RESET}'


def hline(width=None):
    w = width if width is not None else content_width()
    return f'  {C_DIM}{"─" * max(0, w - 4)}{C_RESET}'


def sep_line(label):
    """Render a separator item: fixed-width '────' labels stretch to the
    current terminal width; anything else renders as a dim label."""
    if label and set(label.strip()) == {'─'}:
        return hline()
    return f"  {C_DIM}{trunc(label, content_width() - 2)}{C_RESET}"


def row(label, selected=False):
    """List row. Selected = full-line background highlight (with > glyph
    kept so the 16-color/reverse-video fallback still reads clearly)."""
    w = content_width()
    if selected:
        # strip inner colors so the highlight bar is uniform across the line
        return f'{_c.C_SEL_BG}{fit(" ▸ " + strip_ansi(label), w)}{C_RESET}'
    return fit('   ' + label, w)


def hint_bar(text):
    """Bottom hint/status line."""
    w = content_width()
    return f'{C_DIM}{fit("  " + strip_ansi(text).strip(), w)}{C_RESET}'


def hint_keys(pairs, prefix='', suffix=''):
    """Hint line where each (key, label) renders the key in accent and the
    label dim, so command keys stand out from surrounding text. Optional
    prefix/suffix are dim. ANSI-safe truncation to terminal width."""
    parts = [f"{_c.C_ACCENT}{key}{C_RESET} {C_DIM}{label}{C_RESET}" for key, label in pairs]
    lead = f"  {C_DIM}{prefix}{C_RESET}   " if prefix else "  "
    tail = f"   {C_DIM}{suffix}{C_RESET}" if suffix else ''
    return trunc(lead + "   ".join(parts) + tail, content_width())


def progress_bar(tick, width=36):
    """Indeterminate (knight-rider) progress bar string. Animate by passing
    an incrementing tick."""
    seg = 6
    span = max(1, width - seg)
    pos = tick % (2 * span)
    if pos >= span:
        pos = 2 * span - pos
    return (f"{C_DIM}{'·' * pos}{C_RESET}"
            f"{_c.C_ACCENT}{'━' * seg}{C_RESET}"
            f"{C_DIM}{'·' * (span - pos)}{C_RESET}")


def meter(pct, width=10, color=''):
    """Determinate progress bar: ▕████░░░░░░▏ filled to pct (0-100)."""
    pct = max(0.0, min(100.0, pct))
    filled = round(width * pct / 100)
    return (f"{C_DIM}▕{C_RESET}{color}{'█' * filled}{C_RESET}"
            f"{C_DIM}{'░' * (width - filled)}▏{C_RESET}")


def cols(parts, widths, aligns=None):
    """Join columns at fixed display widths. widths[i] None = remaining space."""
    w = content_width()
    aligns = aligns or ['left'] * len(parts)
    fixed = sum(x for x in widths if x is not None)
    remaining = max(4, w - fixed - 3)
    pieces = []
    for part, width, align in zip(parts, widths, aligns):
        pieces.append(fit(part, remaining if width is None else width, align))
    return ''.join(pieces)
