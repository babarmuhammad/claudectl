import os
import sys
import msvcrt
import time
import ctypes

from .config import W, EFFORTS, EFFORT_LABELS, MODELS, MODEL_LABELS
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


# ── UI primitives ────────────────────────────────────────────

def text_input(prompt, default=''):
    buf = list(default)
    while True:
        os.system('cls')
        print(f"\n  {prompt}")
        print(f"\n  > {''.join(buf)}")
        print(f"\n  ENTER confirm   ESC cancel   BACKSPACE delete")
        raw = msvcrt.getwch()
        if raw in ('\r', '\n'):
            return ''.join(buf).strip()
        elif raw == '\x1b':
            return None
        elif raw == '\x08':
            if buf: buf.pop()
        elif raw in ('\x00', '\xe0'):
            msvcrt.getwch()
        elif raw >= ' ':
            buf.append(raw)


def menu(items, title, footer='', footer_fn=None):
    """items: list of (label, value). value=None = separator.
    footer_fn: callable — polled every 100ms for live updates without flicker."""
    nav_indices = [i for i, (_, v) in enumerate(items) if v is not None]
    if not nav_indices:
        return None
    nav_pos = 0

    def _draw(current_footer):
        os.system('cls')
        cur = nav_indices[nav_pos]
        print(f"\n  {title}\n")
        for i, (label, val) in enumerate(items):
            print(f"  {label}" if val is None else f"  {'>' if i == cur else ' '} {label}")
        print(f"\n  UP/DOWN navigate   ENTER select   ESC back")
        if current_footer:
            print(f"\n{current_footer}")

    def _update_footer_only(old_footer, new_footer):
        """Update only the footer line in-place using ANSI — no cls, no flicker."""
        if not _VT_ENABLED or not old_footer or not new_footer:
            _draw(new_footer)
            return
        sys.stdout.write('\x1b[1A')
        sys.stdout.write('\x1b[G')
        sys.stdout.write('\x1b[2K')
        sys.stdout.write(new_footer)
        sys.stdout.write('\n')
        sys.stdout.flush()

    current_footer = footer_fn() if footer_fn else footer
    _draw(current_footer)
    _last_footer = current_footer

    while True:
        if not msvcrt.kbhit():
            if footer_fn:
                current = footer_fn()
                if current != _last_footer:
                    _update_footer_only(_last_footer, current)
                    _last_footer = current
            time.sleep(0.1)
            continue
        key = ord(msvcrt.getch())
        if key == 224:
            k2 = ord(msvcrt.getch())
            if k2 == 72:   nav_pos = (nav_pos - 1) % len(nav_indices)
            elif k2 == 80: nav_pos = (nav_pos + 1) % len(nav_indices)
        elif key == 13:
            return items[nav_indices[nav_pos]][1]
        elif key == 27:
            return None
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
            os.system('cls')
            print(f"\n  EXTRA PATHS  /  {project_name}\n")
            for i, (label, val) in enumerate(items):
                print(f"  {label}" if val is None else f"  {'>' if i == cur else ' '} {label}")
            print(f"\n  UP/DOWN navigate   ENTER select   DEL remove   ESC back")
            key = ord(msvcrt.getch())
            if key == 224:
                k2 = ord(msvcrt.getch())
                if k2 == 72:   nav_pos = (nav_pos - 1) % len(nav_indices)
                elif k2 == 80: nav_pos = (nav_pos + 1) % len(nav_indices)
                elif k2 == 83:
                    val = items[cur][1]
                    if val and val.startswith('path:'):
                        save_extra_paths(proj_folder, [p for p in paths if p != val[5:]])
                        redraw = True
            elif key == 13:
                val = items[cur][1]
                if val == 'back':
                    return
                elif val == 'add':
                    new_path = text_input("Enter Windows path to add (e.g. C:\\tools\\bin):")
                    if new_path and new_path not in paths:
                        paths.append(new_path)
                        save_extra_paths(proj_folder, paths)
                    redraw = True
            elif key == 27:
                return


def launch_options_menu(project_name):
    """Returns (effort: str, model: str). Empty = use global default."""
    effort_idx = 0
    model_idx  = 0
    field = 0

    while True:
        os.system('cls')
        print(f"\n  LAUNCH OPTIONS  /  {project_name}")
        print(f"\n  {'─' * W}")
        print(f"  {'>' if field == 0 else ' '}  Effort :  [ {EFFORT_LABELS[effort_idx]:<10} ]   ← → cycle")
        print(f"  {'>' if field == 1 else ' '}  Model  :  [ {MODEL_LABELS[model_idx]:<15} ]   ← → cycle")
        print(f"  {'─' * W}")
        print(f"\n  UP/DOWN switch field   ← → cycle   ENTER launch   ESC use defaults")
        key = ord(msvcrt.getch())
        if key == 224:
            k2 = ord(msvcrt.getch())
            if k2 == 72:   field = (field - 1) % 2
            elif k2 == 80: field = (field + 1) % 2
            elif k2 == 75:
                if field == 0: effort_idx = (effort_idx - 1) % len(EFFORTS)
                else:           model_idx  = (model_idx  - 1) % len(MODELS)
            elif k2 == 77:
                if field == 0: effort_idx = (effort_idx + 1) % len(EFFORTS)
                else:           model_idx  = (model_idx  + 1) % len(MODELS)
        elif key == 13:
            return EFFORTS[effort_idx], MODELS[model_idx]
        elif key == 27:
            return '', ''
