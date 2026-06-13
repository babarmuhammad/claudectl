import os
import sys
import atexit
import subprocess

from .config import projects_dir, choice_file, global_claude_md, config_dir
from .config import C_RESET, C_STAR, C_DIM, C_TITLE, C_BOLD
from .config import get_claude_exe, load_settings, save_settings
from .paths import find_actual_path
from .sessions import get_session_info, load_recent_sessions, save_last_session, format_age, scan_sessions
from .ui import menu, launch_options_menu, pause, help_screen, settings_menu
from .session_menu import sessions_menu
from .mcp import mcp_status_line, global_claude_md_menu, mcp_servers
from .usage import usage_status_line
from .ui import _cls
from . import render


def run():
    # ── UTF-8 console ─────────────────────────────────────────────
    os.system('chcp 65001 >nul 2>&1')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    # Alternate screen buffer + hidden cursor for the whole TUI session.
    # Restored before claude.exe takes the console (atexit = safety net).
    render.screen_init()
    atexit.register(render.screen_restore)

    # ── claude.exe availability check ─────────────────────────────
    if not get_claude_exe():
        _cls()
        print(f"\n  {C_TITLE}{C_BOLD}claude.exe not found{C_RESET}\n")
        print(f"  claudectl could not locate Claude Code. Checked:")
        print(f"    - %USERPROFILE%\\.local\\bin\\claude.exe")
        print(f"    - PATH (claude / claude.exe)")
        print(f"    - settings override (~/.claude/claudectl.json)\n")
        print(f"  Install Claude Code:  https://docs.anthropic.com/claude-code")
        print(f"  Or set the path in Settings (⚙) after continuing.\n")
        pause("  Press Enter to continue anyway...")

    # ── discover projects ─────────────────────────────────────────

    entries = []
    if os.path.exists(projects_dir):
        for name in os.listdir(projects_dir):
            proj = os.path.join(projects_dir, name)
            if not os.path.isdir(proj):
                continue
            actual = find_actual_path(name)
            if not actual:
                continue
            mtime = os.path.getmtime(proj)
            entries.append((mtime, actual, name))

    entries.sort(reverse=True)

    if not entries:
        _cls()
        print(f"  No Claude sessions found.\n  Scanned: {projects_dir}")
        pause("\n  Press Enter to exit...")
        sys.exit(0)

    W = 62
    project_items = [(f"{os.path.basename(p) or p:<28}  {p}", p) for _, p, _ in entries]

    recent = load_recent_sessions(5)
    if recent:
        qr_items = []
        for i, sess in enumerate(recent):
            lr_proj    = os.path.basename(sess['project_path']) or sess['project_path']
            lr_preview = sess.get('preview', '') or sess['session_id'][:8] + '...'
            lr_age     = format_age(sess['timestamp'])
            star = '★' if i == 0 else '☆'
            # callable label → re-laid-out on every draw (adapts to resize)
            label = (lambda star=star, proj=lr_proj, prev=lr_preview, age=lr_age:
                     render.cols(
                         [f"{C_STAR}{star}{C_RESET}", proj, prev,
                          f"{C_DIM}({age.strip()}){C_RESET}"],
                         [3, 18, None, 7],
                         aligns=['left', 'left', 'left', 'right']))
            qr_items.append((label, f"__quickresume_{i}__"))
        full_items = qr_items + [(f"{'─' * W}", None)] + project_items
    else:
        full_items = project_items

    full_items = full_items + [
        (f"{'─' * W}", None),
        ('🔍  Search all sessions', '__search_all__'),
        ('⚙  Usage stats', '__usage_stats__'),
        ('⚙  Global CLAUDE.md  /  MCP Analysis', '__global_claude_md__'),
        ('⚙  Settings', '__settings__'),
        ('?  Help', '__help__'),
    ]

    # ── main loop ─────────────────────────────────────────────────

    path = encoded_name = proj_folder = choice = None
    opts = {'effort': '', 'model': '', 'perm': '', 'name': '', 'worktree': ''}

    while True:
        sel = menu(full_items, "SELECT PROJECT",
                   footer_fn=mcp_status_line, banner_fn=usage_status_line)
        if not sel:
            sys.exit(0)

        if sel and sel.startswith('__quickresume_'):
            idx  = int(sel[len('__quickresume_'):-2])
            sess = recent[idx]
            path         = sess['project_path']
            encoded_name = sess['encoded_name']
            proj_folder  = os.path.join(projects_dir, encoded_name)
            choice       = f"resume:{sess['session_id']}"

        elif sel == '__search_all__':
            from .search import global_search
            hit = global_search(entries)
            if not hit:
                continue
            _, path, encoded_name, sid = hit
            proj_folder = os.path.join(projects_dir, encoded_name)
            choice      = f"resume:{sid}"

        elif sel == '__usage_stats__':
            from .stats import usage_dashboard
            usage_dashboard(entries)
            continue

        elif sel == '__global_claude_md__':
            global_claude_md_menu()
            continue

        elif sel == '__settings__':
            settings_menu()
            continue

        elif sel == '__help__':
            help_screen()
            continue

        else:
            path = sel
            encoded_name = next((n for _, p, n in entries if p == path), None)
            if not encoded_name:
                continue   # path not in entries (shouldn't happen) — skip safely
            proj_folder  = os.path.join(projects_dir, encoded_name)

            sessions = scan_sessions(proj_folder)

            project_name = os.path.basename(path) or path
            choice = sessions_menu(sessions, proj_folder, project_name, path)
            if not choice:
                continue

        # Launch options (skip for terminal); ESC = back to main menu
        if choice == 'terminal':
            break
        settings = load_settings()
        proj_def = settings.get('project_defaults', {}).get(encoded_name or '', {})
        opts = launch_options_menu(
            os.path.basename(path) or path,
            defaults={
                'effort':     proj_def.get('effort', settings.get('default_effort', '')),
                'model':      proj_def.get('model', settings.get('default_model', '')),
                'permission': proj_def.get('permission', settings.get('default_permission', '')),
            },
            is_new=(choice == 'new'),
        )
        if opts is None:
            choice = None
            continue
        # Remember per-project launch choices for next time
        if encoded_name:
            settings.setdefault('project_defaults', {})[encoded_name] = {
                'effort': opts['effort'], 'model': opts['model'],
                'permission': opts['perm'],
            }
            save_settings(settings)
        break

    if choice == 'terminal':
        opts = {'effort': '', 'model': '', 'perm': '', 'name': '', 'worktree': ''}

    # Persist last session for quick-resume (resume/fork only)
    if choice and choice not in ('terminal', 'new', 'continue'):
        sid = choice.split('::')[1] if '::' in choice else \
              (choice.split(':')[1] if ':' in choice else '')
        if sid:
            save_last_session(path, encoded_name, sid)

    # Validate action format before handing to the bat launcher
    valid = (
        choice in ('terminal', 'new', 'continue')
        or (choice.startswith('resume:') and len(choice) > 7)
        or (choice.startswith('fork:') and len(choice) > 5)
        or (choice.startswith('resume-named::') and '::' in choice[14:])
    )
    if not valid:
        _cls()
        print(f"\n  Internal error — invalid action: {choice!r}")
        pause("\n  Press Enter to exit...")
        sys.exit(1)
    # '|' is the choice-file delimiter. Strip it from user-typed fields
    # (name/worktree) rather than aborting — only path/config_dir we can't fix.
    opts['name']     = opts['name'].replace('|', '')
    opts['worktree'] = opts['worktree'].replace('|', '')
    if '|' in f"{path}{encoded_name or ''}{config_dir}":
        _cls()
        print(f"\n  Internal error — '|' in project path or config dir; cannot launch.")
        pause("\n  Press Enter to exit...")
        sys.exit(1)

    # cmd reads the choice file in the ANSI codepage — keep bat-bound
    # name/worktree ASCII-safe (direct launch is unaffected).
    if os.environ.get('CLAUDECTL_BAT') == '1':
        opts['name']     = opts['name'].encode('ascii', 'ignore').decode()
        opts['worktree'] = opts['worktree'].encode('ascii', 'ignore').decode()

    # Leave the alt screen before anything else owns the console
    render.screen_restore()

    with open(choice_file, 'w', encoding='utf-8', newline='') as f:
        f.write(build_choice_line(path, encoded_name, choice, opts) + '\r\n')

    # When run via 'Open Repo cmd.bat' the bat reads the choice file and
    # launches claude itself. When run standalone (pipx / `claudectl`),
    # launch directly from Python.
    if os.environ.get('CLAUDECTL_BAT') != '1':
        _direct_launch(path, encoded_name, choice, opts)


def build_choice_line(path, encoded_name, choice, opts):
    """v3 choice-file line. Sentinel '-' for empty fields: cmd's for /f
    collapses consecutive delimiters, which silently shifted fields in the
    old 5-field format (empty effort + set model -> model became effort).
    v3 appends config_dir so the bat launcher can pin CLAUDE_CONFIG_DIR and
    resolve per-project files under the active account's config dir."""
    def sv(x):
        return x if x else '-'
    return '|'.join(['v3', path, encoded_name or '-', choice,
                     sv(opts['effort']), sv(opts['model']), sv(opts['perm']),
                     sv(opts['name']), sv(opts['worktree']), sv(config_dir)])


def _direct_launch(path, encoded_name, choice, opts):
    """Launch claude.exe (or a terminal) directly — used when not started via the bat."""
    from .sessions import read_extra_paths, load_add_dirs

    render.screen_restore()   # idempotent — console must be clean for claude

    proj_folder = os.path.join(projects_dir, encoded_name) if encoded_name else None

    env = os.environ.copy()
    # Pin the account/config dir explicitly — overrides any ambient
    # CLAUDE_CONFIG_DIR claudectl itself may have been launched under.
    env['CLAUDE_CONFIG_DIR'] = config_dir
    extra = read_extra_paths(proj_folder)
    if extra:
        env['PATH'] = ';'.join(extra) + ';' + env.get('PATH', '')

    if choice == 'terminal':
        subprocess.call('cmd /k', cwd=path, env=env, shell=True)
        return

    claude = get_claude_exe()
    if not claude:
        _cls()
        print(f"\n  ✘ claude.exe not found — cannot launch.")
        pause("\n  Press Enter to exit...")
        sys.exit(1)

    args = [claude]
    if choice == 'continue':
        args += ['-c']
    elif choice.startswith('resume:'):
        args += ['-r', choice[7:]]
    elif choice.startswith('resume-named::'):
        args += ['-r', choice[14:].split('::', 1)[0]]
    elif choice.startswith('fork:'):
        args += ['-r', choice[5:], '--fork-session']
    # 'new' → no extra args

    if opts['effort']:
        args += ['--effort', opts['effort']]
    if opts['model']:
        args += ['--model', opts['model']]
    if opts['perm']:
        args += ['--permission-mode', opts['perm']]
    if choice == 'new':
        if opts['name']:
            args += ['-n', opts['name']]
        if opts['worktree'] == '*':
            args += ['-w']
        elif opts['worktree']:
            args += ['-w', opts['worktree']]
    sp_file = os.path.join(proj_folder, 'system-prompt.txt') if proj_folder else ''
    if sp_file and os.path.exists(sp_file):
        args += ['--system-prompt-file', sp_file]
    add_dirs = [d for d in load_add_dirs(proj_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]

    _cls()
    print(f"  Location: {path}")
    print(f"  Action:   {choice}")
    print(f"  {'-' * 42}\n")
    try:
        subprocess.call(args, cwd=path, env=env)
    except Exception as e:
        print(f"\n  ✘ Launch failed: {e}")
        pause("\n  Press Enter to exit...")
        sys.exit(1)
