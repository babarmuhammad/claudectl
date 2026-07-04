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
from . import config as _c


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
    if key == 9:  return ('tab',)
    if 32 <= key <= 126 or key > 127:
        try:
            return ('char', chr(key))
        except ValueError:
            return None
    return None


_term_size = None
_pushback = []   # events peeked by progress scans, preserved for next screen


def push_event(ev):
    """Return an event to the front of the input stream."""
    _pushback.append(ev)


def _size_changed():
    """True when the terminal was resized since the last check."""
    global _term_size
    import shutil
    try:
        sz = shutil.get_terminal_size()
    except Exception:
        return False
    if _term_size is None:
        _term_size = sz
        return False
    if sz != _term_size:
        _term_size = sz
        return True
    return False


def wait_event():
    """Wait for input. Returns a key event, or ('resize',) when the terminal
    size changes — screen loops redraw on any unhandled event, so resizes
    propagate automatically."""
    while True:
        if _pushback:
            return _pushback.pop(0)
        if msvcrt.kbhit():
            ev = _key_event()
            if ev:
                return ev
            continue
        if _size_changed():
            return ('resize',)
        time.sleep(0.03)


def poll_event():
    """Non-blocking: return an event if one is pending, else None."""
    if _pushback:
        return _pushback.pop(0)
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


def run_with_progress(args, crumbs, label, timeout=120, cwd=None):
    """Run a subprocess while showing an animated progress bar; ESC cancels.
    Returns (stdout: str | None, cancelled: bool) — stdout None on
    cancel/timeout/launch failure."""
    import subprocess
    import threading

    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='ignore', cwd=cwd,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return None, False

    # drain stdout on a thread so the pipe can't fill up and deadlock
    chunks = []
    reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()),
                              daemon=True)
    reader.start()

    flush_input()
    start = time.time()
    tick = 0
    while proc.poll() is None:
        if time.time() - start > timeout:
            proc.kill()
            return None, False
        # drain all pending input so keys don't backlog into the next screen
        while True:
            ev = poll_event()
            if not ev:
                break
            if ev[0] == 'esc':
                proc.kill()
                return None, True
        render.render_frame([
            render.header(*crumbs),
            '',
            f"  {label}",
            '',
            '  ' + render.progress_bar(tick),
            f"  {C_DIM}{int(time.time() - start)}s elapsed{C_RESET}",
            '',
            render.hint_keys([('ESC', 'cancel')]),
        ])
        tick += 1
        time.sleep(0.1)

    reader.join(timeout=5)
    return (chunks[0] if chunks else ''), False


def run_with_progress_stdin(args, stdin_text, crumbs, label, timeout=240, cwd=None):
    """Like run_with_progress but feeds the prompt via STDIN (avoids the
    Windows command-line length limit for large prompts). ESC cancels.
    Returns (stdout|None, cancelled)."""
    import subprocess
    import threading
    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='ignore',
            cwd=cwd, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return None, False

    def _feed():
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except Exception:
            pass
    threading.Thread(target=_feed, daemon=True).start()

    chunks = []
    reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()),
                              daemon=True)
    reader.start()

    flush_input()
    start = time.time()
    tick = 0
    while proc.poll() is None:
        if time.time() - start > timeout:
            proc.kill()
            return None, False
        while True:
            ev = poll_event()
            if not ev:
                break
            if ev[0] == 'esc':
                proc.kill()
                return None, True
        render.render_frame([
            render.header(*crumbs), '',
            f"  {label}", '',
            '  ' + render.progress_bar(tick),
            f"  {C_DIM}{int(time.time() - start)}s elapsed{C_RESET}", '',
            render.hint_keys([('ESC', 'cancel')]),
        ])
        tick += 1
        time.sleep(0.1)

    reader.join(timeout=5)
    return (chunks[0] if chunks else ''), False


# ── modal widgets ────────────────────────────────────────────

def confirm(question, danger=False, yes_label='Yes', no_label='No'):
    """Yes/No modal. ←→/↑↓ switch, ENTER confirms, ESC = No. Returns bool."""
    flush_input()
    sel = 0   # 0 = No (safe default), 1 = Yes
    qcol = _c.C_ERR if danger else _c.C_TITLE
    while True:
        opts = [no_label, yes_label]
        row = '   '.join(
            (f"{_c.C_SEL_BG} {o} {C_RESET}" if i == sel else f"  {o}  ")
            for i, o in enumerate(opts))
        frame = [
            render.header('CLAUDECTL', 'CONFIRM'), '',
            f"  {qcol}{render.trunc(question, render.content_width() - 4)}{C_RESET}",
            '', '  ' + row, '',
            render.hint_keys([('←→', 'choose'), ('ENTER', 'confirm'), ('ESC', 'cancel')]),
        ]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] in ('left', 'right', 'up', 'down'):
            sel = 1 - sel
        elif ev[0] == 'enter':
            return sel == 1
        elif ev[0] == 'esc':
            return False
        elif ev[0] == 'char' and ev[1] in 'yY':
            return True
        elif ev[0] == 'char' and ev[1] in 'nN':
            return False


def multiselect(items, title, preselected=None, hint='', view_fn=None):
    """Checkbox list. items: [(label, value)]. SPACE toggles, ENTER confirms,
    'a' all, 'n' none, ESC cancels. Returns set of chosen values or None.
    view_fn: optional callback(value) bound to 'v' to inspect the row."""
    flush_input()
    chosen = set(preselected or set())
    nav = 0
    n = len(items)
    keys = [('SPACE', 'toggle'), ('a', 'all'), ('n', 'none')]
    if view_fn:
        keys.append(('v', 'view'))
    keys += [('ENTER', 'confirm'), ('ESC', 'cancel')]
    while True:
        frame = [render.header('CLAUDECTL', title), '']
        page = max(3, render.frame_height() - 6)
        start = min(max(nav - page // 2, 0), max(0, n - page)) if n > page else 0
        if start > 0:
            frame.append(f"  {C_DIM}… {start} more ↑{C_RESET}")
        for i in range(start, min(start + page, n)):
            label, val = items[i]
            box = '[x]' if val in chosen else '[ ]'
            line = f"{box} {label}"
            frame.append(render.row(line, selected=(i == nav)))
        if start + page < n:
            frame.append(f"  {C_DIM}… {n - start - page} more ↓{C_RESET}")
        frame += ['', render.hint_keys(keys)
                  + f"   {C_DIM}{len(chosen)} selected{C_RESET}"
                  + (f"   {hint}" if hint else '')]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] == 'up':
            nav = (nav - 1) % n if n else 0
        elif ev[0] == 'down':
            nav = (nav + 1) % n if n else 0
        elif ev[0] == 'char' and ev[1] == ' ' and n:
            v = items[nav][1]
            chosen.discard(v) if v in chosen else chosen.add(v)
        elif ev[0] == 'char' and ev[1] == 'a':
            chosen = {v for _, v in items}
        elif ev[0] == 'char' and ev[1] == 'n':
            chosen = set()
        elif ev[0] == 'char' and ev[1] == 'v' and view_fn and n:
            view_fn(items[nav][1])
        elif ev[0] == 'enter':
            return chosen
        elif ev[0] == 'esc':
            return None


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
            render.hint_keys([('ENTER', 'confirm'), ('ESC', 'cancel'), ('BACKSPACE', 'delete')]),
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


def path_completions(text):
    """(base_dir, partial, [child dir names]) for a typed path — directories
    only. Empty text → drive roots. Pure (no UI) for testability."""
    raw = os.path.expandvars(os.path.expanduser(text.strip()))
    if not raw:
        drives = [f"{d}:\\" for d in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                  if os.path.isdir(f"{d}:\\")]
        return '', '', drives
    if raw.endswith(('\\', '/')) or os.path.isdir(raw):
        base, partial = raw, ''
    else:
        base, partial = os.path.dirname(raw) or '.', os.path.basename(raw)
    try:
        names = [d for d in os.listdir(base)
                 if os.path.isdir(os.path.join(base, d))]
    except Exception:
        return base, partial, []
    pl = partial.lower()
    return base, partial, sorted(n for n in names if n.lower().startswith(pl))


def _join_path(base, name):
    return os.path.join(base, name) if base else name


def path_input(prompt, default=''):
    """Text input with live filesystem (directory) auto-completion.
    TAB completes, ↑↓ pick a suggestion, ENTER opens a directory. Returns the
    absolute path of an existing directory, or None on cancel."""
    flush_input()
    buf = list(default)
    sel = -1
    while True:
        text = ''.join(buf)
        base, partial, names = path_completions(text)
        sugg = names[:8]
        if sel >= len(sugg):
            sel = len(sugg) - 1
        cw = render.content_width()
        frame = [
            render.header('CLAUDECTL', 'OPEN PROJECT'), '',
            f"  {C_TITLE}{prompt}{C_RESET}", '',
            f"  {C_SEL}>{C_RESET} {render.trunc(text, cw - 6)}{C_SRCH}▌{C_RESET}", '',
        ]
        for i, n in enumerate(sugg):
            disp = n if n.endswith(('\\', '/')) else n + '\\'
            if i == sel:
                frame.append(f"  {_c.C_ACCENT}▸ {render.trunc(disp, cw - 6)}{C_RESET}")
            else:
                frame.append(f"    {C_DIM}{render.trunc(disp, cw - 6)}{C_RESET}")
        if len(names) > len(sugg):
            frame.append(f"    {C_DIM}… {len(names) - len(sugg)} more{C_RESET}")
        if not names:
            frame.append(f"    {C_DIM}(no matching folders){C_RESET}")
        frame += ['', render.hint_keys([('TAB', 'complete'), ('↑↓', 'suggestions'),
                                        ('ENTER', 'open folder'), ('ESC', 'cancel')])]
        render.render_frame(frame)
        ev = wait_event()

        if ev[0] == 'esc':
            return None
        elif ev[0] == 'enter':
            if sel >= 0 and sugg:
                buf = list(_join_path(base, sugg[sel]) + os.sep)
                sel = -1
                continue
            cand = os.path.abspath(os.path.expandvars(os.path.expanduser(text.strip()))) \
                if text.strip() else ''
            if cand and os.path.isdir(cand):
                return cand
            if len(sugg) == 1:
                buf = list(_join_path(base, sugg[0]) + os.sep)
            else:
                flash("Not a folder — pick a suggestion or type a valid path",
                      ok=False, secs=1.4)
        elif ev[0] == 'tab':
            if sel >= 0 and sugg:
                buf = list(_join_path(base, sugg[sel]) + os.sep)
                sel = -1
            elif sugg:
                lcp = os.path.commonprefix(sugg)
                target = _join_path(base, lcp)
                if len(sugg) == 1:
                    target += os.sep
                buf = list(target)
        elif ev[0] == 'down':
            if sugg:
                sel = (sel + 1) % len(sugg)
        elif ev[0] == 'up':
            if sugg:
                sel = (sel - 1) % len(sugg)
        elif ev[0] == 'back':
            if buf:
                buf.pop()
            sel = -1
        elif ev[0] == 'char':
            buf.append(ev[1])
            sel = -1


def _theme_picker(s):
    """Live theme picker: arrows preview instantly and the cursor stays on the
    selected theme; ESC/ENTER saves the highlighted theme. No trip back to
    Settings between changes."""
    from .config import C_DIM, C_RESET, C_OK
    names = _c.THEME_NAMES
    cur = s.get('theme', 'default')
    idx = names.index(cur) if cur in names else 0

    def _apply(i):
        _c.apply_theme(names[i])
        render.invalidate()

    _apply(idx)
    while True:
        frame = [render.header('CLAUDECTL', 'SETTINGS', 'THEME'), '']
        for i, n in enumerate(names):
            mark = f"{C_OK}●{C_RESET} " if n == s.get('theme') else '  '
            frame.append(render.row(f"{mark}{n}", selected=(i == idx)))
        frame += ['', render.hline(), '',
                  render.hint_keys([('↑↓', 'preview'), ('ENTER', 'select'),
                                    ('ESC', 'back')]),
                  f"  {C_DIM}live preview; restart for full effect{C_RESET}"]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] == 'up':
            idx = (idx - 1) % len(names)
            _apply(idx)
        elif ev[0] == 'down':
            idx = (idx + 1) % len(names)
            _apply(idx)
        elif ev[0] == 'enter':
            s['theme'] = names[idx]
            save_settings(s)
            flash(f"Theme '{names[idx]}' saved", ok=True, secs=1.0)
        elif ev[0] == 'esc':
            # persist whatever is highlighted, then restore & leave
            s['theme'] = names[idx]
            save_settings(s)
            return


def menu(items, title, footer='', footer_fn=None, banner_fn=None):
    """Arrow-key menu with live footer and persistent search bar.
    items: list of (label, value). value=None = non-selectable separator.
    label may be a callable returning str — evaluated on every draw, so
    width-dependent layouts adapt to terminal resizes.
    Any printable key goes to the search bar (no hotkeys in main menu).
    banner_fn: live status line(s) rendered at the TOP, under the header."""

    nav_pos    = 0
    search_str = ''

    def _lab(l):
        return l() if callable(l) else l

    def _filtered():
        if not search_str:
            return items
        fl = search_str.lower()
        result = [(l, v) for l, v in items
                  if v is not None and fl in _lab(l).lower()]
        extras = [(l, v) for l, v in items
                  if v == '__global_claude_md__' and (l, v) not in result]
        return (result + extras) if result else items

    def _nav_idx(disp):
        return [i for i, (_, v) in enumerate(disp) if v is not None]

    def _build(current_footer, current_banner=''):
        disp = _filtered()
        ni   = _nav_idx(disp)
        cur  = ni[min(nav_pos, len(ni) - 1)] if ni else -1

        frame = [render.header('CLAUDECTL', title), '']
        if current_banner:
            for bl in current_banner.split('\n'):
                frame.append(bl)
            frame.append('')

        if search_str:
            frame.append(f"  {C_SRCH}[ {search_str}▌ ]{C_RESET}")
        else:
            frame.append(f"  {C_DIM}[ search... ]{C_RESET}")
        frame.append('')

        # window the item list so hint + footer always fit the terminal
        banner_n = (len(current_banner.split('\n')) + 1) if current_banner else 0
        footer_n = len(current_footer.split('\n')) if current_footer else 1
        fixed = 2 + banner_n + 2 + 2 + footer_n   # header, banner, search, hint area, footer
        avail = max(3, render.frame_height() - fixed)
        n = len(disp)
        start, end = 0, n
        if n > avail:
            vis = max(1, avail - 2)               # room for the … markers
            ci = cur if cur >= 0 else 0
            start = min(max(ci - vis // 2, 0), n - vis)
            end = start + vis
        if start > 0:
            frame.append(f"  {C_DIM}… {start} more ↑{C_RESET}")
        for i in range(start, end):
            label, val = disp[i]
            label = _lab(label)
            if val is None:
                frame.append(render.sep_line(label))
            else:
                frame.append(render.row(label, selected=(i == cur)))
        if end < n:
            frame.append(f"  {C_DIM}… {n - end} more ↓{C_RESET}")

        frame.append('')
        if search_str:
            hint = render.hint_keys([('↑↓', 'navigate'), ('ENTER', 'select'),
                                     ('BACKSPACE', 'delete'), ('ESC', 'clear')])
        else:
            hint = render.hint_keys([('↑↓', 'navigate'), ('ENTER', 'select'),
                                     ('type', 'to search'), ('ESC', 'back')])
        frame.append(hint)
        # footer slot — may be multi-line ('\n'-joined status lines)
        if current_footer:
            for fl in current_footer.split('\n'):
                frame.append(fl)
        else:
            frame.append('')
        return frame

    current_footer = footer_fn() if footer_fn else footer
    current_banner = banner_fn() if banner_fn else ''
    render.render_frame(_build(current_footer, current_banner))
    _last_status = (current_footer, current_banner)
    _last_poll = time.time()

    while True:
        ev = poll_event()
        if ev is None:
            if _size_changed():
                render.render_frame(_build(*_last_status))   # adapt to resize
                time.sleep(0.05)
                continue
            # poll live status sources (MCP / plan usage) twice a second;
            # diff renderer makes the re-render a no-op unless a line changed
            if (footer_fn or banner_fn) and time.time() - _last_poll >= 0.5:
                _last_poll = time.time()
                current_footer = footer_fn() if footer_fn else footer
                current_banner = banner_fn() if banner_fn else ''
                if (current_footer, current_banner) != _last_status:
                    _last_status = (current_footer, current_banner)
                    render.render_frame(_build(current_footer, current_banner))
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

        render.render_frame(_build(*_last_status))


def help_screen():
    """Static hotkey reference. ENTER/ESC returns."""
    frame = [
        render.header('CLAUDECTL', 'HELP'),
        '',
        f"  {C_BOLD}Main screen{C_RESET}",
        f"    ↑↓ navigate    ENTER open project / resume    ESC exit",
        f"    type to search projects    ★/☆ quick-resume recent sessions",
        f"    📂 open new project by path (TAB-complete folders)",
        f"    🔍 search all   ⚙ usage / MCP servers / agents / hooks / settings",
        '',
        f"  {C_BOLD}Sessions screen{C_RESET}",
        f"    ↑↓ navigate    ENTER resume    ESC back    type to filter",
        f"    r  rename                 d  archive / delete",
        f"    f  fork                   v  view transcript (/ search)",
        f"    e  export markdown        i  session info (tokens, cost, model)",
        f"    F  changed files          t  tag session",
        f"    u  project usage          M  memory map (CLAUDE.md hierarchy)",
        f"    p  extra PATH entries     x  add-dirs (--add-dir)",
        f"    c  scaffold CLAUDE.md     a  AI-generate CLAUDE.md",
        f"    g  project agents         w  workspace status (provenance/freshness)",
        f"    n  connections graph + Claude project memory (plexus, ask)",
        f"    s  system prompt          A  archived view    ?  help",
        f"    {C_DIM}AI updates preview a git-style diff before approve — re-view from w{C_RESET}",
        '',
        f"  {C_BOLD}Launch options{C_RESET}",
        f"    ↑↓ field    ← → change    ENTER launch    ESC back",
        f"    effort, model, permissions, agent (+ worktree, name for new sessions)",
        '',
        f"  {C_BOLD}Managers{C_RESET}",
        f"    MCP servers: add/remove/inspect    Hooks: template/toggle",
        f"    Agents: 154-agent library by category, scaffold/AI-generate",
        f"    Per project ('g'): pick agents → copied into .claude/agents/",
        f"    Theme: Settings → Theme",
        '',
        f"  {C_DIM}Settings file: {render.trunc(settings_file, render.content_width() - 20)}{C_RESET}",
        '',
        render.hint_keys([('ENTER / ESC', 'go back')]),
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
        theme = s.get('theme', 'default')
        items = [
            (f"Editor      :  {editor_now}", 'editor'),
            (f"claude.exe  :  {claude_now}", 'claude'),
            (f"Config dir  :  {cfg_now}   {C_DIM}(CLAUDE_CONFIG_DIR / account){C_RESET}", 'config_dir'),
            (f"Effort      :  {eff}   {C_DIM}(preselected in launch options){C_RESET}", 'effort'),
            (f"Model       :  {mod}   {C_DIM}(preselected in launch options){C_RESET}", 'model'),
            (f"Permissions :  {perm}   {C_DIM}(--permission-mode){C_RESET}", 'permission'),
            (f"Theme       :  {theme}", 'theme'),
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
        elif sel == 'theme':
            _theme_picker(s)
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

    flush_input()   # discard keys buffered during whatever ran before
    top = 0
    header_lines = header_lines or []
    query = ''
    matches = []
    pending = None  # event carried over from the coalescing drain

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
        frame += ['', render.hint_keys(
            [('↑↓', 'scroll'), ('←→/SPACE', 'page'), ('/', 'search'),
             ('n/p', 'match'), ('ESC', 'back')],
            prefix=pos, suffix=hint or '')]
        render.render_frame(frame)

        ev = pending if pending else wait_event()
        pending = None
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

        # coalesce queued scroll repeats (held arrows / wheel) into one redraw;
        # any other queued event becomes next iteration's input — never dropped
        while True:
            nxt = poll_event()
            if not nxt:
                break
            if nxt[0] == 'up':
                top -= 1
            elif nxt[0] == 'down':
                top += 1
            else:
                pending = nxt
                break


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
            frame.append(render.hint_keys([('↑↓', 'navigate'), ('ENTER', 'select'),
                                            ('DEL', 'remove'), ('ESC', 'back')]))
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


def launch_options_menu(project_name, defaults=None, is_new=False, agents=None,
                        selected_session_agents=None, memory_status=''):
    """Launch configuration screen.
    Returns None on ESC, else dict {'effort','model','perm','name','worktree','agent'}.
    'worktree': '' = off, '*' = auto-named, other = custom name (new sessions only).
    agents: optional list of agent names → adds an Agent field ('' = none).
    selected_session_agents: refs chosen in the prior agent screen, shown read-only.
    defaults: optional dict with preselected 'effort'/'model'/'permission'."""
    d = defaults or {}
    effort_idx = EFFORTS.index(d.get('effort', '')) if d.get('effort', '') in EFFORTS else 0
    model_idx  = MODELS.index(d.get('model', ''))   if d.get('model', '')  in MODELS  else 0
    perm_idx   = PERMS.index(d.get('permission', '')) if d.get('permission', '') in PERMS else 0
    wt_state   = ''      # '' off | '*' auto | custom name
    name_val   = ''
    agent_opts = [''] + list(agents or [])
    agent_idx  = 0

    base_fields = 3
    new_extra   = 2 if is_new else 0
    agent_field = base_fields + new_extra if len(agent_opts) > 1 else -1
    n_fields    = base_fields + new_extra + (1 if agent_field >= 0 else 0)
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
        if agent_field >= 0:
            al = agent_opts[agent_idx] or '(none)'
            frame.append(
                f"  {sel_c(agent_field)}{'▸' if field == agent_field else ' '}  Lead agent  :  [ {render.trunc(al, 18):<18} ]{C_RESET}   {C_DIM}← → primary --agent (~/.claude/agents){C_RESET}")
        frame.append(render.hline())
        if selected_session_agents:
            frame.append(f"  {_c.C_OK}project agents ({len(selected_session_agents)}){C_RESET}"
                         f"   {C_DIM}'g' in sessions menu to change{C_RESET}")
            names = [r.split('/', 1)[-1] for r in selected_session_agents]
            wrap_w = render.content_width() - 4
            line = ''
            for nm in names:
                piece = (nm + ',')
                if line and render.disp_width(line + ' ' + piece) > wrap_w:
                    frame.append(f"    {C_DIM}{line}{C_RESET}")
                    line = piece
                else:
                    line = (line + ' ' + piece) if line else piece
            if line:
                frame.append(f"    {C_DIM}{line.rstrip(',')}{C_RESET}")
        if memory_status:
            frame.append(f"  {C_DIM}{memory_status}{C_RESET}")
        frame += [
            '',
            render.hint_keys([('↑↓', 'field'), ('← →', 'change'),
                              ('ENTER', 'launch'), ('ESC', 'back')]),
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
            elif field == 4 and is_new:
                v = text_input("Session name (blank = none):", default=name_val)
                if v is not None:
                    name_val = v
            elif field == agent_field:
                agent_idx = (agent_idx + step) % len(agent_opts)
        elif ev[0] == 'enter':
            return {
                'effort': EFFORTS[effort_idx],
                'model':  MODELS[model_idx],
                'perm':   PERMS[perm_idx],
                'name':   name_val if is_new else '',
                'worktree': wt_state if is_new else '',
                'agent':  agent_opts[agent_idx],
            }
        elif ev[0] == 'esc':
            return None
