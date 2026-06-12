import os
import sys
import msvcrt
import time
import ctypes

from .config import W, EFFORTS, EFFORT_LABELS, MODELS, MODEL_LABELS
from .config import PERMS, PERM_LABELS, PERM_RISKY
from .config import C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD, C_GREEN
from .config import load_settings, save_settings, find_editor, get_claude_exe, settings_file
from .config import use_16color_fallback
from .sessions import load_extra_paths, save_extra_paths
from . import render


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
    if not _VT_ENABLED:
        use_16color_fallback()

_enable_vt_mode()


def _cls():
    """Clear screen — ANSI (instant, no subprocess) if VT enabled, else fallback.
    Also invalidates the frame cache: any raw-print screen that starts with
    _cls() forces the next render_frame() to repaint fully."""
    render.invalidate()
    if _VT_ENABLED:
        try:
            sys.stdout.write('\x1b[2J\x1b[H')
            sys.stdout.flush()
        except Exception:
            pass
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
    """Event-based pause (raw output — invalidates the frame cache)."""
    try:
        print(msg)
    except Exception:
        pass
    flush_input()
    while wait_event()[0] not in ('enter', 'esc'):
        pass
    render.invalidate()


def flash(msg, ok=True, secs=0.8):
    """One-line transient feedback shown after an action (✔/✘ + message)."""
    icon = f"{C_GREEN}✔{C_RESET}" if ok else "✘"
    try:
        if render.screen_active():
            rows = render.frame_height()
            sys.stdout.write(f'\x1b[{rows};1H\x1b[K  {icon} {render.trunc(msg, render.content_width() - 6)}')
        else:
            sys.stdout.write(f"\n  {icon} {msg}\n")
        sys.stdout.flush()
    except Exception:
        pass
    time.sleep(secs)
    flush_input()
    render.invalidate()


# ── UI primitives ────────────────────────────────────────────

def text_input(prompt, default=''):
    flush_input()
    buf = list(default)
    while True:
        frame = [
            render.header('CLAUDECTL', 'INPUT'),
            '',
            f"  {C_TITLE}{prompt}{C_RESET}",
            '',
            f"  {C_SEL}>{C_RESET} {''.join(buf)}{C_SRCH}▌{C_RESET}",
            '',
            render.hint_bar("ENTER confirm   ESC cancel   BACKSPACE delete"),
        ]
        render.render_frame(frame)
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

    def _build(current_footer):
        disp = _filtered()
        ni   = _nav_idx(disp)
        cur  = ni[min(nav_pos, len(ni) - 1)] if ni else -1

        frame = [render.header('CLAUDECTL', title), '']

        if search_str:
            frame.append(f"  {C_SRCH}[ {search_str}▌ ]{C_RESET}")
        else:
            frame.append(f"  {C_DIM}[ search... ]{C_RESET}")
        frame.append('')

        for i, (label, val) in enumerate(disp):
            if val is None:
                frame.append(f"  {C_DIM}{render.trunc(label, render.content_width() - 2)}{C_RESET}")
            else:
                frame.append(render.row(label, selected=(i == cur)))

        frame.append('')
        if search_str:
            hint = "↑↓ navigate   ENTER select   BACKSPACE delete   ESC clear"
        else:
            hint = "↑↓ navigate   ENTER select   type to search   ESC back"
        frame.append(render.hint_bar(hint))
        frame.append(current_footer if current_footer else '')   # stable footer slot
        return frame

    current_footer = footer_fn() if footer_fn else footer
    render.render_frame(_build(current_footer))
    _last_footer = current_footer
    _footer_done = False   # True after MCP resolves — stop polling

    while True:
        ev = poll_event()
        if ev is None:
            if footer_fn and not _footer_done:
                current = footer_fn()
                if current != _last_footer:
                    _last_footer = current
                    _footer_done = True   # update exactly once
                    render.render_frame(_build(current))   # diff = footer line only
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

        render.render_frame(_build(_last_footer))


def help_screen():
    """Static hotkey reference. ENTER/ESC returns."""
    frame = [
        render.header('CLAUDECTL', 'HELP'),
        '',
        f"  {C_BOLD}Main screen{C_RESET}",
        f"    ↑↓ navigate    ENTER open project / resume    ESC exit",
        f"    type to search projects    ★/☆ quick-resume recent sessions",
        f"    🔍 search all sessions    ⚙ usage stats / settings / MCP docs",
        '',
        f"  {C_BOLD}Sessions screen{C_RESET}",
        f"    ↑↓ navigate    ENTER resume    ESC back    type to filter",
        f"    r  rename                 d  archive / delete",
        f"    f  fork                   v  view transcript",
        f"    e  export markdown        i  session info (tokens, cost, model)",
        f"    u  project usage stats    A  toggle archived view",
        f"    p  extra PATH entries     x  add-dirs (--add-dir)",
        f"    c  scaffold CLAUDE.md     a  AI-generate CLAUDE.md",
        f"    s  system prompt          ?  this help",
        '',
        f"  {C_BOLD}Launch options{C_RESET}",
        f"    ↑↓ field    ← → change    ENTER launch    ESC back",
        f"    fields: effort, model, permissions (+ worktree, name for new sessions)",
        '',
        f"  {C_DIM}Settings file: {render.trunc(settings_file, render.content_width() - 20)}{C_RESET}",
        '',
        render.hint_bar("ENTER / ESC go back"),
    ]
    render.render_frame(frame)
    flush_input()
    while wait_event()[0] not in ('enter', 'esc'):
        pass


def settings_menu():
    """Edit ~/.claude/claudectl.json interactively."""
    while True:
        s = load_settings()
        wv = render.content_width() - 22
        editor_now = render.trunc(s['editor'] or (find_editor() or 'NOT FOUND'), wv)
        claude_now = render.trunc(s['claude_exe'] or (get_claude_exe() or 'NOT FOUND'), wv)
        cfg_now = render.trunc(s['claude_config_dir'] or 'default (~/.claude)', wv)
        eff = s['default_effort'] or 'default'
        mod = s['default_model'] or 'default'
        perm = s['default_permission'] or 'default'
        items = [
            (f"Editor      :  {editor_now}", 'editor'),
            (f"claude.exe  :  {claude_now}", 'claude'),
            (f"Config dir  :  {cfg_now}   {C_DIM}(CLAUDE_CONFIG_DIR / account){C_RESET}", 'config_dir'),
            (f"Effort      :  {eff}   {C_DIM}(preselected in launch options){C_RESET}", 'effort'),
            (f"Model       :  {mod}   {C_DIM}(preselected in launch options){C_RESET}", 'model'),
            (f"Permissions :  {perm}   {C_DIM}(--permission-mode){C_RESET}", 'permission'),
            (f"{'─' * W}", None),
            (f"Back", 'back'),
        ]
        sel = menu(items, "SETTINGS")
        if not sel or sel == 'back':
            return

        if sel == 'editor':
            v = text_input("Editor path (blank = auto-detect):", default=s['editor'])
            if v is not None:
                if v and not os.path.exists(v):
                    flash(f"Path not found: {v}", ok=False, secs=1.2)
                else:
                    s['editor'] = v
                    save_settings(s)
                    flash("Saved")
        elif sel == 'claude':
            v = text_input("claude.exe path (blank = auto-detect):", default=s['claude_exe'])
            if v is not None:
                if v and not os.path.exists(v):
                    flash(f"Path not found: {v}", ok=False, secs=1.2)
                else:
                    s['claude_exe'] = v
                    save_settings(s)
                    flash("Saved")
        elif sel == 'config_dir':
            v = text_input("CLAUDE_CONFIG_DIR (blank = default ~/.claude):",
                           default=s['claude_config_dir'])
            if v is not None:
                expanded = os.path.expanduser(os.path.expandvars(v)) if v else ''
                if v and not os.path.isdir(expanded):
                    flash(f"Dir not found: {expanded}", ok=False, secs=1.4)
                else:
                    s['claude_config_dir'] = v
                    save_settings(s)
                    flash("Saved — restart claudectl to apply", secs=1.6)
        elif sel in ('effort', 'model', 'permission'):
            values, labels = {
                'effort':     (EFFORTS, EFFORT_LABELS),
                'model':      (MODELS, MODEL_LABELS),
                'permission': (PERMS, PERM_LABELS),
            }[sel]
            pick = menu([(l, v if v else '__default__') for l, v in zip(labels, values)],
                        f"DEFAULT {sel.upper()}")
            if pick is not None:
                s[f'default_{sel}'] = '' if pick == '__default__' else pick
                save_settings(s)
                flash("Saved")


def pager(crumbs, lines, hint='', header_lines=None, extra_keys=(),
          marks=None, mark_label='msg'):
    """Scrollable frame-rendered pager with in-content search.
    crumbs: breadcrumb tuple for the header bar.
    lines: pre-wrapped content lines (ANSI ok).
    header_lines: optional pinned lines under the header.
    extra_keys: chars returned to the caller when pressed (e.g. ('i','e')).
    marks: optional sorted line indices of logical units (e.g. message starts) —
           the position indicator then counts units instead of raw lines.
    Returns None on exit, or the pressed extra key."""
    import bisect

    top = 0
    header_lines = header_lines or []
    query = ''
    matches = []

    def _find(q):
        ql = q.lower()
        return [i for i, ln in enumerate(lines)
                if ql in render.strip_ansi(ln).lower()]

    while True:
        page = max(4, render.frame_height() - len(header_lines) - 6)
        top = max(0, min(top, max(0, len(lines) - page)))

        if marks:
            cur = bisect.bisect_right(marks, top)
            pos = f"{mark_label} {max(1, cur)}/{len(marks)}"
        else:
            pos = f"{min(top + page, len(lines))}/{len(lines)}"
        if query:
            mpos = bisect.bisect_right(matches, top)
            pos += f"   {C_SRCH}'{query}' {mpos}/{len(matches)}{C_RESET}"

        match_set = set(matches) if query else ()
        frame = [render.header(*crumbs), '']
        frame += header_lines
        if header_lines:
            frame.append(render.hline())
        for idx in range(top, min(top + page, len(lines))):
            ln = lines[idx]
            if idx in match_set:
                frame.append(f"{C_SRCH}▌{C_RESET}" +
                             render.fit(' ' + ln, render.content_width() - 1))
            else:
                frame.append(render.fit('  ' + ln, render.content_width()))
        frame += ['', render.hint_bar(
            f"{pos}   ↑↓ scroll   ←→/SPACE page   / search   n/p match   ESC back"
            + (f"   {hint}" if hint else ''))]
        render.render_frame(frame)

        ev = wait_event()
        if ev[0] == 'up':
            top -= 1
        elif ev[0] == 'down':
            top += 1
        elif ev[0] == 'left':
            top -= page
        elif ev[0] == 'right':
            top += page
        elif ev[0] == 'char' and ev[1] == ' ':
            top += page
        elif ev[0] == 'char' and ev[1] == '/':
            q = text_input("Search transcript:", default=query)
            if q is not None:
                query = q
                matches = _find(query) if query else []
                if query and not matches:
                    flash(f"No matches for '{query}'", ok=False)
                elif matches:
                    top = matches[0]
        elif ev[0] == 'char' and ev[1] == 'n' and matches:
            nxt = [m for m in matches if m > top]
            top = nxt[0] if nxt else matches[0]          # wrap to first
        elif ev[0] == 'char' and ev[1] == 'p' and matches:
            prv = [m for m in matches if m < top]
            top = prv[-1] if prv else matches[-1]        # wrap to last
        elif ev[0] == 'esc':
            if query:
                query = ''
                matches = []
            else:
                return None
        elif ev[0] == 'enter':
            return None
        elif ev[0] == 'char' and ev[1] in extra_keys:
            return ev[1]


# ── feature menus ────────────────────────────────────────────

def paths_menu(proj_folder, project_name, filename='extra-paths.txt', title='EXTRA PATHS'):
    """Edit a per-project line-list file (extra-paths.txt or add-dirs.txt)."""
    def _load():
        try:
            with open(os.path.join(proj_folder, filename), 'r', encoding='utf-8') as f:
                return [l.strip() for l in f if l.strip()]
        except Exception:
            return []

    def _save(paths):
        with open(os.path.join(proj_folder, filename), 'w', encoding='utf-8') as f:
            f.write('\n'.join(paths))

    while True:
        paths = _load()
        items = [(f"{'─' * W}", None)]
        for p in paths:
            items.append((render.trunc(p, render.content_width() - 8), f"path:{p}"))
        if not paths:
            items.append((f"(none configured)", None))
        items += [(f"{'─' * W}", None), (f"+ Add new path", 'add'), (f"Back", 'back')]

        nav_indices = [i for i, (_, v) in enumerate(items) if v is not None]
        nav_pos = 0
        redraw = False
        while not redraw:
            cur = nav_indices[nav_pos]
            frame = [render.header('CLAUDECTL', project_name, title), '']
            for i, (label, val) in enumerate(items):
                if val is None:
                    frame.append(f"  {C_DIM}{label}{C_RESET}")
                else:
                    frame.append(render.row(label, selected=(i == cur)))
            frame.append('')
            frame.append(render.hint_bar("↑↓ navigate   ENTER select   DEL remove   ESC back"))
            render.render_frame(frame)

            ev = wait_event()
            activate = None
            if ev[0] == 'up':
                nav_pos = (nav_pos - 1) % len(nav_indices)
            elif ev[0] == 'down':
                nav_pos = (nav_pos + 1) % len(nav_indices)
            elif ev[0] == 'del':
                val = items[cur][1]
                if val and val.startswith('path:'):
                    _save([p for p in paths if p != val[5:]])
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
                    _save(paths)
                redraw = True


def launch_options_menu(project_name, defaults=None, is_new=False):
    """Launch configuration screen.
    Returns None on ESC, else dict {'effort','model','perm','name','worktree'}.
    'worktree': '' = off, '*' = auto-named, other = custom name (new sessions only).
    defaults: optional dict with preselected 'effort'/'model'/'permission'."""
    d = defaults or {}
    effort_idx = EFFORTS.index(d.get('effort', '')) if d.get('effort', '') in EFFORTS else 0
    model_idx  = MODELS.index(d.get('model', ''))   if d.get('model', '')  in MODELS  else 0
    perm_idx   = PERMS.index(d.get('permission', '')) if d.get('permission', '') in PERMS else 0
    wt_state   = ''      # '' off | '*' auto | custom name
    name_val   = ''

    n_fields = 5 if is_new else 3
    field = 0

    def _wt_label():
        if not wt_state:    return 'off'
        if wt_state == '*': return 'auto'
        return wt_state

    while True:
        def sel_c(i):
            return C_SEL if field == i else C_DIM

        perm_label = PERM_LABELS[perm_idx]
        perm_color = '\033[93m' if PERM_LABELS[perm_idx] in PERM_RISKY else ''
        frame = [
            render.header('CLAUDECTL', project_name, 'LAUNCH OPTIONS'),
            '',
            render.hline(),
            f"  {sel_c(0)}{'▸' if field == 0 else ' '}  Effort      :  [ {EFFORT_LABELS[effort_idx]:<18} ]{C_RESET}   {C_DIM}← → cycle{C_RESET}",
            f"  {sel_c(1)}{'▸' if field == 1 else ' '}  Model       :  [ {MODEL_LABELS[model_idx]:<18} ]{C_RESET}   {C_DIM}← → cycle{C_RESET}",
            f"  {sel_c(2)}{'▸' if field == 2 else ' '}  Permissions :  [ {perm_color}{perm_label:<18}{C_RESET}{sel_c(2)} ]{C_RESET}   {C_DIM}← → cycle{C_RESET}",
        ]
        if is_new:
            frame += [
                f"  {sel_c(3)}{'▸' if field == 3 else ' '}  Worktree    :  [ {render.trunc(_wt_label(), 18):<18} ]{C_RESET}   {C_DIM}← → cycle, → on 'custom'{C_RESET}",
                f"  {sel_c(4)}{'▸' if field == 4 else ' '}  Name        :  [ {render.trunc(name_val or '(none)', 18):<18} ]{C_RESET}   {C_DIM}→ edit{C_RESET}",
            ]
        frame += [
            render.hline(),
            '',
            render.hint_bar("↑↓ field   ← → change   ENTER launch   ESC back"),
        ]
        render.render_frame(frame)

        ev = wait_event()
        if ev[0] == 'up':
            field = (field - 1) % n_fields
        elif ev[0] == 'down':
            field = (field + 1) % n_fields
        elif ev[0] in ('left', 'right'):
            step = -1 if ev[0] == 'left' else 1
            if field == 0:
                effort_idx = (effort_idx + step) % len(EFFORTS)
            elif field == 1:
                model_idx = (model_idx + step) % len(MODELS)
            elif field == 2:
                perm_idx = (perm_idx + step) % len(PERMS)
            elif field == 3:
                # cycle off -> auto -> custom… -> off
                if not wt_state:
                    if step > 0:
                        wt_state = '*'
                    else:
                        v = text_input("Worktree name (blank = cancel):")
                        wt_state = v if v else ''
                elif wt_state == '*':
                    if step > 0:
                        v = text_input("Worktree name (blank = cancel):")
                        wt_state = v if v else '*'
                    else:
                        wt_state = ''
                else:
                    wt_state = '' if step > 0 else '*'
            elif field == 4:
                v = text_input("Session name (blank = none):", default=name_val)
                if v is not None:
                    name_val = v
        elif ev[0] == 'enter':
            return {
                'effort': EFFORTS[effort_idx],
                'model':  MODELS[model_idx],
                'perm':   PERMS[perm_idx],
                'name':   name_val if is_new else '',
                'worktree': wt_state if is_new else '',
            }
        elif ev[0] == 'esc':
            return None
