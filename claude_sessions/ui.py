import os
import sys
import msvcrt
import time
import ctypes

from .config import W, EFFORTS, EFFORT_LABELS, MODELS, MODEL_LABELS
from .config import C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD
from .sessions import load_extra_paths, save_extra_paths


# ── VT mode ──────────────────────────────────────────────────

_VT_ENABLED = False

def _enable_vt_mode():
    global _VT_ENABLED
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                _VT_ENABLED = True
    except Exception:
        pass

_enable_vt_mode()


def _cls():
    """Clear screen — ANSI (instant, no subprocess) if VT enabled, else fallback."""
    if _VT_ENABLED:
        sys.stdout.write('\x1b[2J\x1b[H')
        sys.stdout.flush()
    else:
        os.system('cls')


# ── keyboard input ───────────────────────────────────────────
# Events returned by wait_event()/poll_event():
#   ('up',) ('down',) ('left',) ('right',) ('enter',) ('esc',)
#   ('back',) ('del',) ('char', c)

def _key_event():
    key = ord(msvcrt.getch())
    if key in (0, 224):
        k2 = ord(msvcrt.getch())
        return {72: ('up',), 80: ('down',), 75: ('left',), 77: ('right',),
                83: ('del',)}.get(k2, None)
    if key == 13: return ('enter',)
    if key == 27: return ('esc',)
    if key == 8:  return ('back',)
    if 32 <= key <= 126 or key > 127:
        try:
            return ('char', chr(key))
        except ValueError:
            return None
    return None


def wait_event():
    """Block until a meaningful input event arrives."""
    while True:
        ev = _key_event()
        if ev:
            return ev


def poll_event():
    """Non-blocking: return an event if one is pending, else None."""
    if msvcrt.kbhit():
        return _key_event()
    return None


def flush_input():
    while msvcrt.kbhit():
        msvcrt.getch()


def pause(msg='  Press Enter to continue...'):
    """Event-based pause."""
    print(msg)
    flush_input()
    while wait_event()[0] not in ('enter', 'esc'):
        pass


# ── UI primitives ────────────────────────────────────────────

def text_input(prompt, default=''):
    flush_input()
    buf = list(default)
    while True:
        _cls()
        print(f"\n  {C_TITLE}{prompt}{C_RESET}")
        print(f"\n  {C_SEL}>{C_RESET} {''.join(buf)}_")
        print(f"\n  {C_DIM}ENTER confirm   ESC cancel   BACKSPACE delete{C_RESET}")
        ev = wait_event()
        if ev[0] == 'enter':
            return ''.join(buf).strip()
        elif ev[0] == 'esc':
            return None
        elif ev[0] == 'back':
            if buf: buf.pop()
        elif ev[0] == 'char':
            buf.append(ev[1])


def menu(items, title, footer='', footer_fn=None):
    """Arrow-key menu with live footer and persistent search bar.
    items: list of (label, value). value=None = non-selectable separator.
    Any printable key goes to the search bar (no hotkeys in main menu)."""

    nav_pos    = 0
    search_str = ''

    def _filtered():
        if not search_str:
            return items
        fl = search_str.lower()
        result = [(l, v) for l, v in items
                  if v is not None and fl in l.lower()]
        extras = [(l, v) for l, v in items
                  if v == '__global_claude_md__' and (l, v) not in result]
        return (result + extras) if result else items

    def _nav_idx(disp):
        return [i for i, (_, v) in enumerate(disp) if v is not None]

    def _draw(current_footer):
        disp = _filtered()
        ni   = _nav_idx(disp)
        if not ni:
            return
        pos = min(nav_pos, len(ni) - 1)
        cur = ni[pos]

        _cls()
        print(f"\n  {C_TITLE}{C_BOLD}{title}{C_RESET}\n")

        # Search bar — always visible
        if search_str:
            print(f"  {C_SRCH}[ {search_str}▌ ]{C_RESET}\n")
        else:
            print(f"  {C_DIM}[ search... ]{C_RESET}\n")

        for i, (label, val) in enumerate(disp):
            if val is None:
                print(f"  {C_DIM}{label}{C_RESET}")
            elif i == cur:
                print(f"  {C_SEL}>{C_RESET} {label}")
            else:
                print(f"    {label}")

        if search_str:
            hint = f"  {C_DIM}↑↓ navigate   ENTER select   BACKSPACE delete   ESC clear{C_RESET}"
        else:
            hint = f"  {C_DIM}↑↓ navigate   ENTER select   type to search   ESC back{C_RESET}"
        print(f"\n{hint}")
        if current_footer:
            print(f"\n{current_footer}")

    current_footer = footer_fn() if footer_fn else footer
    _draw(current_footer)
    _last_footer = current_footer
    _footer_done = False   # True after MCP resolves — stop polling

    def _update_footer_inline(new_footer):
        """Update only the footer line using ANSI cursor — no full redraw, no flash."""
        if not _VT_ENABLED:
            return   # skip; footer shows on next keypress redraw
        line = new_footer if new_footer else ''
        sys.stdout.write('\x1b[1A\x1b[G\x1b[2K')
        sys.stdout.write(line)
        sys.stdout.write('\n')
        sys.stdout.flush()

    while True:
        ev = poll_event()
        if ev is None:
            if footer_fn and not _footer_done:
                current = footer_fn()
                if current != _last_footer:
                    _last_footer = current
                    _footer_done = True   # update exactly once
                    _update_footer_inline(current)
            time.sleep(0.05)
            continue

        disp = _filtered()
        ni   = _nav_idx(disp)

        if ev[0] in ('up', 'down'):
            if ni:
                step = -1 if ev[0] == 'up' else 1
                nav_pos = (min(nav_pos, len(ni) - 1) + step) % len(ni)
        elif ev[0] == 'enter':
            if ni:
                return disp[ni[min(nav_pos, len(ni) - 1)]][1]
            return None
        elif ev[0] == 'esc':
            if search_str:
                search_str = ''
                nav_pos    = 0
            else:
                return None
        elif ev[0] == 'back':
            if search_str:
                search_str = search_str[:-1]
                nav_pos    = 0
        elif ev[0] == 'char':
            search_str += ev[1]
            nav_pos    = 0

        _draw(_last_footer)


# ── feature menus ────────────────────────────────────────────

def paths_menu(proj_folder, project_name):
    while True:
        paths = load_extra_paths(proj_folder)
        items = [(f"{'─' * W}", None)]
        for p in paths:
            items.append((f"  {p}", f"path:{p}"))
        if not paths:
            items.append((f"  (no extra paths configured)", None))
        items += [(f"{'─' * W}", None), (f"  + Add new path", 'add'), (f"  Back", 'back')]

        nav_indices = [i for i, (_, v) in enumerate(items) if v is not None]
        nav_pos = 0
        redraw = False
        while not redraw:
            cur = nav_indices[nav_pos]
            _cls()
            print(f"\n  {C_TITLE}{C_BOLD}EXTRA PATHS  /  {project_name}{C_RESET}\n")
            for i, (label, val) in enumerate(items):
                if val is None:
                    print(f"  {C_DIM}{label}{C_RESET}")
                elif i == cur:
                    print(f"  {C_SEL}>{C_RESET} {label}")
                else:
                    print(f"    {label}")
            print(f"\n  {C_DIM}↑↓ navigate   ENTER select   DEL remove   ESC back{C_RESET}")

            ev = wait_event()
            activate = None
            if ev[0] == 'up':
                nav_pos = (nav_pos - 1) % len(nav_indices)
            elif ev[0] == 'down':
                nav_pos = (nav_pos + 1) % len(nav_indices)
            elif ev[0] == 'del':
                val = items[cur][1]
                if val and val.startswith('path:'):
                    save_extra_paths(proj_folder, [p for p in paths if p != val[5:]])
                    redraw = True
            elif ev[0] == 'enter':
                activate = items[cur][1]
            elif ev[0] == 'esc':
                return

            if activate == 'back':
                return
            elif activate == 'add':
                new_path = text_input("Enter Windows path to add (e.g. C:\\tools\\bin):")
                if new_path and new_path not in paths:
                    paths.append(new_path)
                    save_extra_paths(proj_folder, paths)
                redraw = True


def launch_options_menu(project_name):
    """Returns (effort: str, model: str), empty = global default.
    Returns None on ESC (caller should go back instead of launching)."""
    effort_idx = 0
    model_idx  = 0
    field = 0

    while True:
        _cls()
        print(f"\n  {C_TITLE}{C_BOLD}LAUNCH OPTIONS  /  {project_name}{C_RESET}")
        print(f"\n  {C_DIM}{'─' * W}{C_RESET}")
        e_sel = C_SEL if field == 0 else C_DIM
        m_sel = C_SEL if field == 1 else C_DIM
        print(f"  {e_sel}{'>' if field == 0 else ' '}  Effort :  [ {EFFORT_LABELS[effort_idx]:<10} ]{C_RESET}   {C_DIM}← → cycle{C_RESET}")
        print(f"  {m_sel}{'>' if field == 1 else ' '}  Model  :  [ {MODEL_LABELS[model_idx]:<15} ]{C_RESET}   {C_DIM}← → cycle{C_RESET}")
        print(f"  {C_DIM}{'─' * W}{C_RESET}")
        print(f"\n  {C_DIM}↑↓ switch field   ← → cycle   ENTER launch   ESC back{C_RESET}")

        ev = wait_event()
        if ev[0] in ('up', 'down'):
            field = (field + 1) % 2
        elif ev[0] in ('left', 'right'):
            step = -1 if ev[0] == 'left' else 1
            if field == 0: effort_idx = (effort_idx + step) % len(EFFORTS)
            else:           model_idx  = (model_idx  + step) % len(MODELS)
        elif ev[0] == 'enter':
            return EFFORTS[effort_idx], MODELS[model_idx]
        elif ev[0] == 'esc':
            return None
