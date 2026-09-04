import os
import sys
import time

from .config import W, EFFORTS, EFFORT_LABELS
# MODELS/MODEL_LABELS are deliberately NOT imported here. They are the
# bundled floor; the two screens that need a model list bind the live one
# from _c.models() as a local. Importing them would give a future reader
# a list that silently predates the running catalogue.
from .config import PERMS, PERM_LABELS, PERM_RISKY, THINKING_CAPS, THINKING_LABELS
from .config import C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD, C_GREEN
from .config import load_settings, save_settings, find_editor, get_claude_exe, settings_file
from .config import use_16color_fallback
from .sessions import load_extra_paths, save_extra_paths
from . import render
from . import config as _c
from . import term


# ── VT mode ──────────────────────────────────────────────────

_VT_ENABLED = term.enable_vt()
if not _VT_ENABLED:
    use_16color_fallback()


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
        term.clear()


# ── keyboard input ───────────────────────────────────────────
# Events returned by wait_event()/poll_event():
#   ('up',) ('down',) ('left',) ('right',) ('enter',) ('esc',)
#   ('back',) ('del',) ('char', c)

def _key_event():
    # via the module, never `= term.key_event` — a by-value binding at import
    # is the bug this codebase keeps re-learning, and it would freeze the
    # backend the test harness swaps.
    return term.key_event()


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
        if term.kbhit():
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
    if term.kbhit():
        return _key_event()
    return None


def flush_input():
    while term.kbhit():
        term.getch()


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


def run_with_progress(args, crumbs, label, timeout=120, cwd=None, env=None):
    """Run a subprocess while showing an animated progress bar; ESC cancels.
    Returns (stdout: str | None, cancelled: bool) — stdout None on
    cancel/timeout/launch failure.

    Silent/background mode (memory._tls.silent, set by every GUI job
    thread): plain subprocess.run, no render loop at all. CONFIRMED
    NECESSARY, not optional — the render loop's clear-screen fallback
    (os.system('cls'), used when VT mode isn't available) spawns a real
    console per tick on a console-less job thread. At ~10 ticks/sec for up
    to `timeout` seconds that looked like terminals endlessly opening and
    closing until the whole app was killed (confirmed via Plan→Execute).
    Same idiom memory._claude_stdin() already used for exactly this reason
    — this makes it apply to every run_with_progress[_stdin] caller at
    once (agents.py AI-gen, mcp.py MCP analysis, plan_execute.py, …)
    instead of requiring each call site to remember to check.

    env: full environment dict for the subprocess (e.g. CLAUDE_CONFIG_DIR
    pointing at a non-default account). None = inherit. Its sibling
    run_with_progress_stdin already had this; the gap meant every `claude mcp`
    call ran against whichever account claudectl inherited."""
    import subprocess
    from . import memory
    if getattr(memory._tls, 'silent', False):
        from .gui_api import _run_cancellable, JobCancelled
        try:
            return _run_cancellable(args, cwd=cwd, env=env, timeout=timeout), False
        except JobCancelled:
            raise                # a user cancel must mark the job 'cancelled',
                                 # not degrade into a generic failure
        except Exception:
            return None, False
    import threading
    from . import quota

    # The silent path is guarded inside _run_cancellable above, so each path is
    # guarded exactly once and a switched account is never re-asked about.
    env, blocked = quota.preflight(args, env)
    if blocked:
        return None, False

    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='ignore', cwd=cwd, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        return None, False

    # drain stdout on a thread so the pipe can't fill up and deadlock
    chunks = []
    reader = threading.Thread(target=lambda: chunks.append(proc.stdout.read()),
                              daemon=True)
    reader.start()
    # stderr on its own thread for the same reason, and it is captured rather
    # than DEVNULLed because throwing it away is what made every failure read
    # as "No output from Claude" — including a rate-limited account.
    errs = []
    errdr = threading.Thread(target=lambda: errs.append(proc.stderr.read()),
                             daemon=True)
    errdr.start()

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
    errdr.join(timeout=5)
    if proc.returncode:
        _note_failure(args, env, proc.returncode, errs, chunks)
        return None, False
    return (chunks[0] if chunks else ''), False


def _note_failure(args, env, code, errs, chunks=()):
    """One latch for a failed child, the same one gui_api._run_cancellable
    writes — so a caller has ONE place to read why, not two.

    `chunks` is stdout. It matters because the refusal Claude Code cares most
    about — the session/weekly limit — is printed to STDOUT inside the
    `--output-format json` envelope, not to stderr, so a stderr-only latch said
    "claude exited 1: (no output)" for it.
    """
    from . import memory as _m, quota
    from .gui_api import _claude_failure_reason
    err = ''.join(c for c in errs if c).strip()
    out = _claude_failure_reason(''.join(c for c in chunks if c))
    _m.last_call_error = 'claude exited %s: %s' % (
        code, (err or out or '(no output)')[:300])
    # the raw text of BOTH streams, for the same reason _run_cancellable passes
    # the envelope: a marker may sit in a field the sentence does not carry
    quota.note_failure(env, err + '\n' + ''.join(c for c in chunks if c))
    from . import events
    events.record('subprocess', _m.last_call_error,
                  detail=' '.join(str(a) for a in list(args)[:2]))


def run_with_progress_stdin(args, stdin_text, crumbs, label, timeout=240, cwd=None, env=None):
    """Like run_with_progress but feeds the prompt via STDIN (avoids the
    Windows command-line length limit for large prompts). ESC cancels.
    Returns (stdout|None, cancelled). Silent/background mode: see
    run_with_progress's docstring — same reasoning, same fix.

    env: full environment dict for the subprocess (e.g. to point
    CLAUDE_CONFIG_DIR at a non-default account). None = inherit as before."""
    import subprocess
    from . import memory
    if getattr(memory._tls, 'silent', False):
        from .gui_api import _run_cancellable, JobCancelled
        try:
            return _run_cancellable(args, input_text=stdin_text, cwd=cwd, env=env, timeout=timeout), False
        except JobCancelled:
            raise                # a user cancel must mark the job 'cancelled',
                                 # not degrade into a generic failure
        except Exception:
            return None, False
    import threading
    from . import quota

    env, blocked = quota.preflight(args, env)     # see run_with_progress
    if blocked:
        return None, False

    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore',
            cwd=cwd, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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
    errs = []
    errdr = threading.Thread(target=lambda: errs.append(proc.stderr.read()),
                             daemon=True)
    errdr.start()

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
    errdr.join(timeout=5)
    if proc.returncode:
        _note_failure(args, env, proc.returncode, errs, chunks)
        return None, False
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
            label = _c.theme_label(n)
            name = n if label == n else f"{n}  {C_DIM}{label}{C_RESET}"
            frame.append(render.row(f"{mark}{name}", selected=(i == idx)))
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


#: two columns is what makes the help screen fit its frame; one would push the
#: sessions block past a 35-row terminal.
HELP_COLS = 2
#: the narrowest terminal the help screen is WRITTEN for — not the narrowest it
#: survives (render.content_width() floors at 40 and everything truncates
#: there). Key blurbs must read whole at this width, which is the budget
#: test_parity_gate.py checks them against. Lowering it is a decision about the
#: product, which is exactly why it is here and not in a test fixture.
HELP_MIN_COLS = 80


def help_blurb_budget():
    """Characters one help-grid cell gives a blurb at the current width.

    Public and derived, not a constant repeated in the test: the first cut had
    the test asserting 33 while this computed 32, so the gate against truncation
    passed with four cells truncated.
    """
    return max(28, (render.content_width() - 8) // HELP_COLS - 3)


def _session_key_lines(cols=HELP_COLS):
    """The sessions-screen key list, GENERATED from session_menu.ACTIONS.

    It used to be typed here, and it had drifted three keys behind the code:
    `X` (plan→execute), `K` (inject context) and `R` (code review) were absent,
    and for `R` this was the second of two missing entries, so code review had
    no discoverable entry point anywhere in the product. Same rule that deleted
    `docs/gui-audit.md`: a hand-kept copy of what the code already states is a
    copy that will be wrong.

    Imported inside the function because session_menu imports this module.
    """
    from .session_menu import key_rows
    # `?` is the screen you are reading, so it is not one of its own entries.
    rows = [(k, b) for k, b in key_rows() if k != '?']
    # Follow the terminal rather than a fixed column: these blurbs are the ONLY
    # description of each key now, so a width that truncates half of them on a
    # normal window would lose what merging the three lists was meant to keep.
    width = help_blurb_budget() if cols == HELP_COLS else max(
        28, (render.content_width() - 8) // cols - 3)
    out = []
    for i in range(0, len(rows), cols):
        cells = []
        for k, blurb in rows[i:i + cols]:
            cells.append(f"{k}  {render.trunc(blurb, width):<{width}}")
        out.append('    ' + '  '.join(cells).rstrip())
    return out


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
        f"    {C_DIM}/  opens the same list, type-to-filter{C_RESET}",
    ] + _session_key_lines() + [
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


def _failover_label(s):
    models = [m for m in (s.get('failover_models') or []) if str(m).strip()]
    if not models:
        return 'off'
    return f"{len(models)} fallback{'s' if len(models) != 1 else ''} on :{s.get('failover_port') or 20129}"


def _failover_menu():
    """The failover proxy was GUI-only: three settings and start/stop with no TUI
    equivalent at all, so a TUI-only user could not see it existed."""
    from . import failover
    while True:
        s = load_settings()
        models = [m for m in (s.get('failover_models') or []) if str(m).strip()]
        port = int(s.get('failover_port') or 20129)
        live = failover.is_ready(port)
        items = [
            (f"Fallback models :  {', '.join(models) if models else C_DIM + '(none — failover off)' + C_RESET}", 'models'),
            (f"Port            :  {port}", 'port'),
            (f"Log window      :  {'hidden' if s.get('failover_quiet') else 'visible'}   "
             f"{C_DIM}(the routing log is the feature — keep it visible){C_RESET}", 'quiet'),
            (f"{'─' * W}", None),
            ((f"{C_GREEN}running{C_RESET} — stop it" if live else 'not running — start it'),
             'stop' if live else 'start'),
            (f"{'─' * W}", None),
            ('Back', 'back'),
        ]
        sel = menu(items, "MODEL FAILOVER")
        if not sel or sel == 'back':
            return
        if sel == 'models':
            v = text_input("Fallback models, comma-separated (blank = off):",
                           default=', '.join(models))
            if v is not None:
                s['failover_models'] = [m.strip() for m in v.split(',') if m.strip()]
                save_settings(s)
                flash("Saved")
        elif sel == 'port':
            v = text_input("Proxy port:", default=str(port))
            if v is not None:
                try:
                    s['failover_port'] = int(v)
                except ValueError:
                    flash("Enter a port number", ok=False, secs=1.2)
                    continue
                save_settings(s)
                flash("Saved — restart the proxy for it to take effect", secs=2)
        elif sel == 'quiet':
            s['failover_quiet'] = not s.get('failover_quiet')
            save_settings(s)
            flash(f"Log window {'hidden' if s['failover_quiet'] else 'visible'}")
        elif sel == 'start':
            ok, msg = failover.ensure_running(s)
            flash(msg if not ok else f"Failover proxy running at {msg}",
                  ok=ok, secs=2.5)
        elif sel == 'stop':
            ok, msg = failover.stop_running()
            flash(msg, ok=ok, secs=2)


def _budget_label(s):
    cap = s.get('headless_budget_usd') or 0
    return f"${cap:g} per call" if cap else 'off'


_UPDATE_LABELS = {
    'notify': 'tell me',
    'auto':   'install on quit',
    'off':    'off (no outbound check)',
}


def _update_label(s):
    v = s.get('auto_update') or 'notify'
    return _UPDATE_LABELS.get(v, v)


def _automode_label():
    """What the accounts currently start sessions in — 'mixed' when they
    disagree, which is the state a machine is in after any hand-editing."""
    try:
        from . import automode
        modes = {automode.default_mode(d) or '(inherit)'
                 for _n, d in _c.all_config_dirs()}
    except Exception:
        return 'unavailable'
    return modes.pop() if len(modes) == 1 else f'mixed ({len(modes)})'


def _automode_menu():
    """Per-account starting mode + the trusted-infrastructure entries.

    Writes each ACCOUNT's settings.json: the classifier deliberately ignores an
    `autoMode` block in a project file, and `defaultMode: auto` does not take
    effect from one either.
    """
    from . import automode
    while True:
        accts = list(_c.all_config_dirs())
        items = []
        for name, d in accts:
            mode = automode.default_mode(d) or '(inherit built-in)'
            n_env = len([e for e in automode.environment(d) if e != '$defaults'])
            items.append((f"{name:<12}  {mode:<20}{C_DIM}{n_env} trusted entry(ies){C_RESET}",
                          ('acct', name, d)))
        items += [(f"{'─' * W}", None),
                  ("Show the rules the classifier actually uses", ('rules', 'config', '')),
                  ("Show the built-in rules", ('rules', 'defaults', '')),
                  ("Back", 'back')]
        sel = menu(items, "AUTO MODE")
        if not sel or sel == 'back':
            return
        kind = sel[0]
        if kind == 'rules':
            import json as _json
            ok, data = (automode.config_json() if sel[1] == 'config'
                        else automode.defaults_json())
            body = (_json.dumps(data, indent=2) if ok and not isinstance(data, str)
                    else str(data))
            # pager takes (crumbs, lines) — crumbs first
            pager(('CLAUDECTL', 'AUTO MODE', sel[1].upper()), body.splitlines())
            continue
        _name, cfgdir = sel[1], sel[2]
        what = menu([("Starting permission mode", 'mode'),
                     ("Trusted infrastructure (autoMode.environment)", 'env'),
                     ("Reset this account's auto-mode config", 'reset'),
                     ("Back", 'back')], f"AUTO MODE — {_name}")
        if not what or what == 'back':
            continue
        if what == 'mode':
            pick = menu([(l or 'inherit', v if v else '__unset__')
                         for v, l in zip(PERMS, PERM_LABELS)], "STARTING MODE")
            if pick is not None:
                ok, msg = automode.set_default_mode(
                    '' if pick == '__unset__' else pick, cfgdir)
                flash(msg, ok=ok, secs=1.8)
        elif what == 'env':
            # "$defaults" is never shown: it is re-added on save, and a user
            # deleting it would silently discard every built-in trust slot
            cur = [e for e in automode.environment(cfgdir) if e != '$defaults']
            v = text_input("Trusted infrastructure (one per line, plain English; "
                           "blank = defaults only):", default=' | '.join(cur))
            if v is not None:
                ok, msg = automode.set_environment(
                    [x.strip() for x in v.split('|')], cfgdir)
                flash(msg, ok=ok, secs=1.8)
        elif what == 'reset':
            ok, msg = automode.reset(cfgdir)
            flash(str(msg), ok=ok, secs=2)


#: the settings rows that are a pick from a fixed list. Keyed by the SETTINGS
#: KEY, which is also the row id — the old scheme wrote `s[f'default_{sel}']`,
#: so the id and the key it wrote were never the same string.
def _default_pickers(models, model_labels):
    return {
        'default_effort':         (EFFORTS, EFFORT_LABELS),
        'default_model':          (models, model_labels),
        'default_permission':     (PERMS, PERM_LABELS),
        'default_max_thinking':   (THINKING_CAPS, THINKING_LABELS),
        'default_subagent_model': (models, model_labels),
    }


def settings_menu():
    """Edit ~/.claude/claudectl.json interactively."""
    while True:
        s = load_settings()
        # The LIVE roster shadows the module-level floor for this screen. The
        # three saved pins are passed in so a model Anthropic has retired keeps
        # its row here instead of reading back as 'default' — see config.models.
        MODELS, MODEL_LABELS = _c.models(s.get('default_model', ''),
                                         s.get('default_subagent_model', ''),
                                         s.get('extract_model', ''))
        wv = render.content_width() - 22
        editor_now = render.trunc(s['editor'] or (find_editor() or 'NOT FOUND'), wv)
        claude_now = render.trunc(s['claude_exe'] or (get_claude_exe() or 'NOT FOUND'), wv)
        cfg_now = render.trunc(s['claude_config_dir'] or 'default (~/.claude)', wv)
        eff = s['default_effort'] or 'default'
        mod = s['default_model'] or 'default'
        _pv = s['default_permission'] or ''
        perm = next((l for v, l in zip(PERMS, PERM_LABELS) if v == _pv), _pv or 'account default')
        think = s.get('default_max_thinking') or 'default'
        submod = next((l for v, l in zip(MODELS, MODEL_LABELS)
                       if v == s.get('default_subagent_model', '')), 'default')
        xmod = next((l for v, l in zip(MODELS, MODEL_LABELS)
                     if v == s.get('extract_model', '')), 'default')
        theme = s.get('theme', 'default')
        items = [
            # every id here IS the settings key it writes. It was not: 'claude',
            # 'config_dir', 'headless_budget' and five hidden `default_<id>`
            # rewrites meant no test could join this screen to the GUI's list of
            # the same settings, and four of them turned out to have no GUI
            # control at all.
            (f"Editor      :  {editor_now}", 'editor'),
            (f"claude.exe  :  {claude_now}", 'claude_exe'),
            (f"Config dir  :  {cfg_now}   {C_DIM}(CLAUDE_CONFIG_DIR / account){C_RESET}", 'claude_config_dir'),
            (f"Effort      :  {eff}   {C_DIM}(preselected in launch options){C_RESET}", 'default_effort'),
            (f"Model       :  {mod}   {C_DIM}(preselected in launch options){C_RESET}", 'default_model'),
            (f"Permissions :  {perm}   {C_DIM}({_c.PERM_PROFILES.get(_pv, '--permission-mode')}){C_RESET}", 'default_permission'),
            (f"Think cap   :  {think}   {C_DIM}(MAX_THINKING_TOKENS — save tokens){C_RESET}", 'default_max_thinking'),
            (f"Subagent mdl:  {submod}   {C_DIM}(CLAUDE_CODE_SUBAGENT_MODEL){C_RESET}", 'default_subagent_model'),
            (f"Economy mdl :  {xmod}   {C_DIM}(claudectl's own memory/gen calls — cuts cost){C_RESET}", 'extract_model'),
            (f"Auto mode   :  {_automode_label()}   {C_DIM}(classifier config, per account){C_RESET}", 'automode'),
            (f"Budget cap  :  {_budget_label(s)}   {C_DIM}(--max-budget-usd on claudectl's own calls){C_RESET}", 'headless_budget_usd'),
            (f"Theme       :  {theme}", 'theme'),
            (f"Interface   :  {s.get('ui_mode', 'tui').upper()}   {C_DIM}(TUI here / GUI in browser — or run --gui){C_RESET}", 'ui_mode'),
            (f"Failover    :  {_failover_label(s)}   {C_DIM}(retry a dead model instead of hanging){C_RESET}", 'failover'),
            (f"Updates     :  {_update_label(s)}   {C_DIM}(new claudectl releases + the model list){C_RESET}", 'auto_update'),
            (f"Notifications: {'on' if s.get('notifications', True) else 'off'}   {C_DIM}(desktop toast when a long background job ends){C_RESET}", 'notifications'),
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
        elif sel == 'claude_exe':
            v = text_input("claude.exe path (blank = auto-detect):", default=s['claude_exe'])
            if v is not None:
                if v and not os.path.exists(v):
                    flash(f"Path not found: {v}", ok=False, secs=1.2)
                else:
                    s['claude_exe'] = v
                    save_settings(s)
                    flash("Saved")
        elif sel == 'claude_config_dir':
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
        elif sel == 'automode':
            _automode_menu()
        elif sel == 'headless_budget_usd':
            v = text_input("Max $ per claudectl headless call (0 = no cap):",
                           default=str(s.get('headless_budget_usd') or 0))
            if v is not None:
                try:
                    s['headless_budget_usd'] = max(0.0, float(v or 0))
                    save_settings(s)
                    flash("Saved")
                except ValueError:
                    flash("Not a number", ok=False, secs=1.4)
        elif sel == 'theme':
            _theme_picker(s)
        elif sel == 'failover':
            _failover_menu()
        elif sel == 'auto_update':
            pick = menu([('Tell me when a new claudectl is released', 'notify'),
                         ('Install it automatically when I quit claudectl', 'auto'),
                         ('Off — never check (also stops the model-list refresh)', 'off')],
                        "UPDATES")
            if pick:
                s['auto_update'] = pick
                save_settings(s)
                flash(f"Updates: {_UPDATE_LABELS.get(pick, pick)}", secs=2)
        elif sel == 'notifications':
            pick = menu([('On — tell me when a long background job finishes', 'on'),
                         ('Off — no desktop notifications', 'off')],
                        "NOTIFICATIONS")
            if pick:
                s['notifications'] = (pick == 'on')
                save_settings(s)
                flash(f"Notifications: {pick}")
        elif sel == 'ui_mode':
            pick = menu([('TUI — this terminal interface', 'tui'),
                         ('GUI — web app in your browser', 'gui')],
                        "DEFAULT INTERFACE")
            if pick:
                s['ui_mode'] = pick
                save_settings(s)
                flash(f"Default interface: {pick.upper()} — applies next start "
                      f"(--tui/--gui always override)", secs=2.2)
        elif sel in _default_pickers(MODELS, MODEL_LABELS):
            values, labels = _default_pickers(MODELS, MODEL_LABELS)[sel]
            pick = menu([(l, v if v else '__default__') for l, v in zip(labels, values)],
                        f"{sel.replace('_', ' ').upper()}")
            if pick is not None:
                s[sel] = '' if pick == '__default__' else pick
                save_settings(s)
                flash("Saved")
        elif sel == 'extract_model':
            pick = menu([(l, v if v else '__default__') for l, v in zip(MODEL_LABELS, MODELS)],
                        "ECONOMY MODEL  (claudectl's own generation calls)")
            if pick is not None:
                s['extract_model'] = '' if pick == '__default__' else pick
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
        # extra_keys wins over the built-in n/p search-match navigation: a caller
        # that asked for 'n' must get 'n', otherwise the binding it registered
        # silently stops working whenever a search happens to be active.
        elif ev[0] == 'char' and ev[1] in extra_keys:
            return ev[1]
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
                        selected_session_agents=None, memory_status='', account_opts=None):
    """Launch configuration screen.
    Returns None on ESC, else dict {'effort','model','perm','name','worktree','agent'}.
    'worktree': '' = off, '*' = auto-named, other = custom name (new sessions only).
    agents: optional list of agent names → adds an Agent field ('' = none).
    selected_session_agents: refs chosen in the prior agent screen, shown read-only.
    defaults: optional dict with preselected 'effort'/'model'/'permission'."""
    d = defaults or {}
    # Resolved once per screen, not per frame. Every index below is a cursor
    # into this list, so a roster that changed length between two keystrokes
    # would move the selection under the user's hand; and the saved model is
    # passed in so a retired pin keeps its position rather than collapsing to
    # index 0 (= account default) and being written back as an erasure.
    MODELS, MODEL_LABELS = _c.models(d.get('model', ''), d.get('subagent_model', ''))
    # Resolved with the roster, for the same reason and at the same cost: a pin
    # Anthropic has retired is still IN the list above (dropping it would reset
    # the field behind the user's back), so this screen has to be what says it
    # is gone — otherwise the launch fails with an API error that never
    # mentions where the id came from.
    try:
        from . import models as _mods
        _retired = set(_mods.retired_pins())
    except Exception:
        _retired = set()
    effort_idx = EFFORTS.index(d.get('effort', '')) if d.get('effort', '') in EFFORTS else 0
    model_idx  = MODELS.index(d.get('model', ''))   if d.get('model', '')  in MODELS  else 0
    perm_idx   = PERMS.index(d.get('permission', '')) if d.get('permission', '') in PERMS else 0
    think_idx  = THINKING_CAPS.index(d.get('max_thinking', '')) if d.get('max_thinking', '') in THINKING_CAPS else 0
    sub_idx    = MODELS.index(d.get('subagent_model', '')) if d.get('subagent_model', '') in MODELS else 0
    wt_state   = ''      # '' off | '*' auto | custom name
    name_val   = ''
    agent_opts = [''] + list(agents or [])
    agent_idx  = 0
    # Account: editable when CREATING a new session (pick which account it lives
    # under); read-only when resuming, since an existing session can only be
    # resumed under the config dir it was recorded in.
    acct_opts   = list(account_opts or [])
    acct_label  = acct_opts[0][0] if acct_opts else ''
    acct_idx    = 0
    acct_editable = is_new and len(acct_opts) > 1

    base_fields = 3
    new_extra   = 2 if is_new else 0
    agent_field = base_fields + new_extra if len(agent_opts) > 1 else -1
    after_agent = base_fields + new_extra + (1 if agent_field >= 0 else 0)
    # editable account picker (new sessions, >1 account) sits before the
    # launch-economy fields; those are appended last so the arithmetic holds
    acct_field  = after_agent if acct_editable else -1
    think_field = after_agent + (1 if acct_editable else 0)
    sub_field   = think_field + 1
    n_fields    = sub_field + 1
    field = 0

    def _wt_label():
        if not wt_state:    return 'off'
        if wt_state == '*': return 'auto'
        return wt_state

    def _model_rows():
        # index-aligned to MODELS so the ◉ marker tracks model_idx; '' = default
        out = []
        for mid in MODELS:
            if not mid:
                out.append(('', 'default', '', '', 'account model', ''))
                continue
            prof = _c.profile(mid)
            lbl = MODEL_LABELS[MODELS.index(mid)]
            if prof:
                out.append((mid, lbl, _c.cost_bar(mid), _c.cap_bar(mid),
                            prof['best_for'], _c.swe_str(mid)))
            else:
                out.append((mid, lbl, '', '', '', ''))
        return out

    def _slider(idx, n, width=30):
        cells = ['─'] * width
        ticks = [round(j * (width - 1) / (n - 1)) for j in range(n)]
        for t in ticks:
            cells[t] = '·'
        cells[ticks[idx]] = '●'
        return '├' + ''.join(cells) + '┤'

    def _apply_preset(i):
        nonlocal model_idx, effort_idx, think_idx, sub_idx
        if not (0 <= i < len(_c.LAUNCH_PRESETS)):
            return
        name, _desc, f = _c.LAUNCH_PRESETS[i]
        if f.get('model') in MODELS:            model_idx  = MODELS.index(f['model'])
        if f.get('effort') in EFFORTS:          effort_idx = EFFORTS.index(f['effort'])
        if f.get('max_thinking') in THINKING_CAPS:   think_idx = THINKING_CAPS.index(f['max_thinking'])
        if f.get('subagent_model') in MODELS:   sub_idx    = MODELS.index(f['subagent_model'])
        flash(f"Preset: {name}", secs=1.0)

    def _show_guide():
        lines = [render.header('CLAUDECTL', project_name, 'MODEL GUIDE'), '',
                 render.hline(),
                 f"  {C_DIM}model         SWE    cost    cap      best for{C_RESET}"]
        for _mid, lbl, cb, capb, bf, sw in _c.model_card_rows():
            lines.append(f"  {lbl:<12} {sw:<6} {cb:<6} {capb:<7} {bf}")
        lines += ['', f"  {C_DIM}effort  (raise before switching model tier — high/xhigh ≈ next tier up){C_RESET}"]
        for ev_, el_ in zip(EFFORTS, EFFORT_LABELS):
            if not ev_:
                continue
            lines.append(f"  {el_:<7} {_c.EFFORT_PROFILES.get(ev_, '')}")
        lines += ['', f"  {C_DIM}presets{C_RESET}"]
        for pn, pd, _f in _c.LAUNCH_PRESETS:
            lines.append(f"  {pn:<15} {C_DIM}{pd}{C_RESET}")
        lines += ['',
                  f"  {C_DIM}Sonnet at xhigh can cost more than Opus·high — don't over-crank effort.{C_RESET}",
                  '', render.hint_keys([('any key', 'back')])]
        render.render_frame(lines)
        wait_event()

    while True:
        def sel_c(i, field=field):
            return C_SEL if field == i else C_DIM

        perm_label = PERM_LABELS[perm_idx]
        perm_color = '\033[93m' if PERM_LABELS[perm_idx] in PERM_RISKY else ''
        eff_cur = EFFORTS[effort_idx]
        preset_name = _c.active_preset({
            'model': MODELS[model_idx], 'effort': eff_cur,
            'max_thinking': THINKING_CAPS[think_idx], 'subagent_model': MODELS[sub_idx]})
        strip = [(f"{C_SEL}{pn}{C_RESET}" if pn == preset_name else f"{C_DIM}{pn}{C_RESET}")
                 for pn, _d, _f in _c.LAUNCH_PRESETS]
        slcol = C_SEL if field == 0 else C_DIM
        frame = [
            render.header('CLAUDECTL', project_name, 'START SESSION' if is_new else 'LAUNCH OPTIONS'),
            '',
            f"  {C_DIM}Quick start{C_RESET}   " + f" {C_DIM}·{C_RESET} ".join(strip)
            + f"   {C_DIM}1-4{C_RESET}",
            render.hline(),
            f"  {sel_c(0)}{'▸' if field == 0 else ' '}  Effort{C_RESET}  {C_DIM}default{C_RESET} "
            f"{slcol}{_slider(effort_idx, len(EFFORTS))}{C_RESET} {C_DIM}max{C_RESET}   "
            f"{sel_c(0)}{EFFORT_LABELS[effort_idx]}{C_RESET} {C_DIM}· {_c.EFFORT_PROFILES.get(eff_cur, '')}{C_RESET}",
        ]
        bcol = C_SEL if field == 1 else C_DIM
        frame.append(f"  {bcol}{'▸' if field == 1 else ' '}  Model{C_RESET}   {C_DIM}← → cycle{C_RESET}")
        frame.append(f"    {bcol}╭{'─' * 46}╮{C_RESET}")
        for mid, lbl, cb, capb, bf, sw in _model_rows():
            marked = (MODELS[model_idx] == mid)
            body = f"{'◉' if marked else '○'} {lbl:<10} {sw:<4}{cb:<6}{capb:<6}{bf}"
            lc = C_GREEN if marked else C_DIM
            frame.append(f"    {bcol}│{C_RESET} {lc}{render.trunc(body, 44):<44}{C_RESET} {bcol}│{C_RESET}")
        frame.append(f"    {bcol}╰{'─' * 46}╯{C_RESET}")
        frame.append(
            f"  {sel_c(2)}{'▸' if field == 2 else ' '}  Permissions :  [ {perm_color}{perm_label:<18}{C_RESET}{sel_c(2)} ]{C_RESET}   "
            f"{C_DIM}{_c.PERM_PROFILES.get(PERMS[perm_idx], '')}{C_RESET}")
        if is_new:
            frame += [
                f"  {sel_c(3)}{'▸' if field == 3 else ' '}  Worktree    :  [ {render.trunc(_wt_label(), 18):<18} ]{C_RESET}   {C_DIM}← → cycle, → on 'custom'{C_RESET}",
                f"  {sel_c(4)}{'▸' if field == 4 else ' '}  Name        :  [ {render.trunc(name_val or '(none)', 18):<18} ]{C_RESET}   {C_DIM}→ edit{C_RESET}",
            ]
        if agent_field >= 0:
            al = agent_opts[agent_idx] or '(none)'
            frame.append(
                f"  {sel_c(agent_field)}{'▸' if field == agent_field else ' '}  Lead agent  :  [ {render.trunc(al, 18):<18} ]{C_RESET}   {C_DIM}← → primary --agent (~/.claude/agents){C_RESET}")
        if acct_editable:
            al = acct_opts[acct_idx][0]
            frame.append(
                f"  {sel_c(acct_field)}{'▸' if field == acct_field else ' '}  Account     :  [ {render.trunc(al, 18):<18} ]{C_RESET}   {C_DIM}← → account for new session{C_RESET}")
        elif acct_label:
            frame.append(
                f"  {C_DIM}   Account     :  [ {render.trunc(acct_label, 18):<18} ]   read-only — switch in ⚙ Accounts{C_RESET}")
        frame.append(
            f"  {sel_c(think_field)}{'▸' if field == think_field else ' '}  Think cap   :  [ {THINKING_LABELS[think_idx]:<18} ]{C_RESET}   {C_DIM}← → MAX_THINKING_TOKENS{C_RESET}")
        frame.append(
            f"  {sel_c(sub_field)}{'▸' if field == sub_field else ' '}  Subagents   :  [ {MODEL_LABELS[sub_idx]:<18} ]{C_RESET}   {C_DIM}← → CLAUDE_CODE_SUBAGENT_MODEL{C_RESET}")
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
        adv_level, adv_msg = _c.advise(MODELS[model_idx], eff_cur)
        if MODELS[model_idx] in _retired:
            adv_level = 'warn'
            adv_msg = (f"Anthropic no longer offers {MODELS[model_idx]} — "
                       f"this launch will fail. Pick another model.")
        adv_c = {'ok': C_GREEN, 'tip': C_SRCH, 'warn': _c.C_WARN}.get(adv_level, C_DIM)
        adv_tag = {'ok': '', 'tip': 'tip: ', 'warn': 'note: '}.get(adv_level, '')
        # Separate from advise(): a permission note depends on model AND mode,
        # and advise() is precomputed by the GUI as a model x effort matrix.
        pn_level, pn_msg = _c.perm_note(PERMS[perm_idx], MODELS[model_idx])
        frame += [
            '',
            f"  {adv_c}{render.trunc(adv_tag + adv_msg, render.content_width() - 4)}{C_RESET}",
        ]
        if pn_msg:
            pn_c = {'warn': _c.C_WARN}.get(pn_level, C_SRCH)
            frame.append(
                f"  {pn_c}{render.trunc('note: ' + pn_msg, render.content_width() - 4)}{C_RESET}")
        frame += [
            render.hint_keys([('↑↓', 'field'), ('← →', 'change'), ('1-4', 'preset'),
                              ('?', 'guide'), ('ENTER', 'launch'), ('ESC', 'back')]),
        ]
        render.render_frame(frame)

        ev = wait_event()
        if ev[0] == 'up':
            field = (field - 1) % n_fields
        elif ev[0] == 'down':
            field = (field + 1) % n_fields
        elif ev[0] == 'char' and ev[1] in '1234':
            _apply_preset(int(ev[1]) - 1)
        elif ev[0] == 'char' and ev[1] == 'e':
            _apply_preset(0)   # Economy alias (back-compat)
        elif ev[0] == 'char' and ev[1] == '?':
            _show_guide()
        elif ev[0] in ('left', 'right'):
            step = -1 if ev[0] == 'left' else 1
            if field == 0:
                effort_idx = (effort_idx + step) % len(EFFORTS)
            elif field == 1:
                model_idx = (model_idx + step) % len(MODELS)
            elif field == 2:
                perm_idx = (perm_idx + step) % len(PERMS)
            elif field == 3 and is_new:
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
            elif field == acct_field:
                acct_idx = (acct_idx + step) % len(acct_opts)
            elif field == think_field:
                think_idx = (think_idx + step) % len(THINKING_CAPS)
            elif field == sub_field:
                sub_idx = (sub_idx + step) % len(MODELS)
        elif ev[0] == 'enter':
            return {
                'effort': EFFORTS[effort_idx],
                'model':  MODELS[model_idx],
                'perm':   PERMS[perm_idx],
                'name':   name_val if is_new else '',
                'worktree': wt_state if is_new else '',
                'agent':  agent_opts[agent_idx],
                # new session → the account the user picked; resume → '' (active,
                # i.e. the config dir the session was recorded under)
                'cfgdir': acct_opts[acct_idx][1] if acct_editable else '',
                'max_thinking':   THINKING_CAPS[think_idx],
                'subagent_model': MODELS[sub_idx],
            }
        elif ev[0] == 'esc':
            return None
