import os
import sys
import atexit
import subprocess

from .config import projects_dir, choice_file, global_claude_md, config_dir
from .config import C_RESET, C_STAR, C_DIM, C_TITLE, C_BOLD, C_NAME
from .config import get_claude_exe, load_settings, save_settings
from .paths import find_actual_path
from .sessions import (get_session_info, load_recent_sessions, save_last_session,
                       format_age, scan_sessions, load_name, get_session_title)
from .ui import menu, launch_options_menu, pause, help_screen, settings_menu
from .session_menu import sessions_menu
from .mcp import mcp_status_line, global_claude_md_menu, mcp_servers, mcp_manager_menu
from .usage import usage_status_line
from .ui import _cls
from . import render


def _workspace_status_cli():
    """`claudectl workspace status` — resolve the project from cwd and print."""
    from .paths import encode_component
    from . import workspace
    cwd = os.path.abspath(os.getcwd())
    encoded = encode_component(cwd)
    proj_folder = os.path.join(projects_dir, encoded)
    if not os.path.isdir(proj_folder):
        proj_folder = None
    workspace.print_workspace_status(cwd, proj_folder)


def _recall_cli(query):
    """`claudectl recall "<query>"` — print the task-relevant memory subgraph.
    This is the on-demand surface the CLAUDE.md micro-digest points Claude to."""
    from .paths import encode_component
    from . import recall
    cwd = os.path.abspath(os.getcwd())
    proj_folder = os.path.join(projects_dir, encode_component(cwd))
    if not os.path.isdir(proj_folder):
        proj_folder = None
    budget = load_settings().get('memory_budget', 600)
    r = recall.retrieve(cwd, proj_folder, query, budget)
    print(r['text'] if not r['empty'] else '(no relevant project memory)')


def run():
    # `claudectl workspace status` — scriptable, no TUI
    if sys.argv[1:3] == ['workspace', 'status']:
        _workspace_status_cli()
        return
    # `claudectl recall "<query>"` — scriptable, no TUI
    if len(sys.argv) >= 3 and sys.argv[1] == 'recall':
        _recall_cli(' '.join(sys.argv[2:]))
        return

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
            sid        = sess['session_id']
            pf         = os.path.join(projects_dir, sess.get('encoded_name', ''))
            jsonl      = os.path.join(pf, f"{sid}.jsonl")
            # Session name: manual rename > AI transcript title > preview > id.
            lr_name    = (load_name(pf, sid) or get_session_title(jsonl)
                          or sess.get('preview', '') or sid[:8] + '…')
            lr_age     = format_age(sess['timestamp'])
            star = '★' if i == 0 else '☆'
            # callable label → re-laid-out on every draw (adapts to resize)
            label = (lambda star=star, proj=lr_proj, nm=lr_name, age=lr_age:
                     render.cols(
                         [f"{C_STAR}{star}{C_RESET}", proj,
                          f"{C_NAME}{nm}{C_RESET}",
                          f"{C_DIM}({age.strip()}){C_RESET}"],
                         [3, 18, None, 7],
                         aligns=['left', 'left', 'left', 'right']))
            qr_items.append((label, f"__quickresume_{i}__"))
        full_items = qr_items + [(f"{'─' * W}", None)] + project_items
    else:
        full_items = project_items

    full_items = full_items + [
        (f"{'─' * W}", None),
        ('📂  Open new project by path…', '__open_path__'),
        ('🔍  Search all sessions', '__search_all__'),
        ('⚙  Usage stats', '__usage_stats__'),
        ('⚙  MCP servers', '__mcp__'),
        ('⚙  Agents', '__agents__'),
        ('⚙  Hooks', '__hooks__'),
        ('⚙  Global CLAUDE.md  /  MCP Analysis', '__global_claude_md__'),
        ('⚙  Accounts (switch / run 2 at once)', '__accounts__'),
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

        elif sel == '__open_path__':
            from .ui import path_input
            from .paths import encode_component
            p = path_input("Open Claude in which folder?  (TAB to complete)")
            if not p:
                continue
            path = os.path.abspath(p)
            encoded_name = encode_component(path)
            proj_folder  = os.path.join(projects_dir, encoded_name)
            project_name = os.path.basename(path) or path
            choice = 'new'

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

        elif sel == '__mcp__':
            mcp_manager_menu()
            continue

        elif sel == '__agents__':
            from .agents import agents_menu
            agents_menu(None)
            continue

        elif sel == '__hooks__':
            from .hooks import hooks_menu
            hooks_menu()
            continue

        elif sel == '__global_claude_md__':
            global_claude_md_menu()
            continue

        elif sel == '__accounts__':
            from .accounts import accounts_menu
            accounts_menu()
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
        from .agents import list_all_agent_names, sync_project_agents
        from .sessions import load_session_agents

        # Library agents are selected at PROJECT level ('g' in the sessions
        # menu) and live in <project>/.claude/agents/. They apply to every
        # launch of the project, so the launch flow just reflects + re-syncs
        # the current project selection rather than prompting per session.
        chosen_refs = load_session_agents(proj_folder).get('__project__', []) if proj_folder else []

        try:
            from . import recall
            mem_line = recall.memory_status_line(path, proj_folder, settings)
        except Exception:
            mem_line = ''
        # per-launch account choice (active first) — only when extra accounts exist
        acct_opts = []
        try:
            from .accounts import _accounts
            accs = _accounts(settings)
            if len(accs) > 1:
                def _abs(dd):
                    return (os.path.expanduser(os.path.expandvars(dd)) if dd
                            else os.path.expanduser('~/.claude'))
                acct_opts = ([(n, _abs(dd)) for n, dd, a in accs if a]
                             + [(n, _abs(dd)) for n, dd, a in accs if not a])
        except Exception:
            acct_opts = []
        opts = launch_options_menu(
            os.path.basename(path) or path,
            defaults={
                'effort':     proj_def.get('effort', settings.get('default_effort', '')),
                'model':      proj_def.get('model', settings.get('default_model', '')),
                'permission': proj_def.get('permission', settings.get('default_permission', '')),
            },
            is_new=(choice == 'new'),
            agents=list_all_agent_names(path),
            selected_session_agents=chosen_refs,
            memory_status=mem_line,
            account_opts=acct_opts,
        )
        if opts is None:
            choice = None
            continue
        # Re-sync in case the project .claude/agents/ drifted; safe no-op when
        # the selection already matches. Inline --agents is avoided because its
        # JSON overruns the Windows command line for real agents.
        sync_project_agents(path, chosen_refs)
        # Remember per-project launch choices
        if encoded_name:
            settings.setdefault('project_defaults', {})[encoded_name] = {
                'effort': opts['effort'], 'model': opts['model'],
                'permission': opts['perm'],
            }
            save_settings(settings)
        break

    if choice == 'terminal':
        opts = {'effort': '', 'model': '', 'perm': '', 'name': '', 'worktree': '', 'agent': ''}
    opts.setdefault('agent', '')
    opts.setdefault('agents_json', '')

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
    opts['agent']    = opts.get('agent', '').replace('|', '')
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

    # Launch is unified in Python: the bat re-invokes this script with --launch
    # (so it can pass big --agents JSON the cmd choice-file can't hold), and the
    # pipx/standalone path launches inline here.
    if os.environ.get('CLAUDECTL_BAT') != '1':
        _direct_launch(path, encoded_name, choice, opts)


def build_choice_line(path, encoded_name, choice, opts):
    """v5 choice-file line. Sentinel '-' for empty fields: cmd's for /f
    collapses consecutive delimiters, which silently shifted fields in the
    old 5-field format. v3 added config_dir; v4 the --agent name; v5 a path
    to a temp JSON file of selected subagents (--agents, built per session)."""
    def sv(x):
        return x if x else '-'
    return '|'.join(['v5', path, encoded_name or '-', choice,
                     sv(opts['effort']), sv(opts['model']), sv(opts['perm']),
                     sv(opts['name']), sv(opts['worktree']), sv(config_dir),
                     sv(opts.get('agent', '')), sv(opts.get('agents_json', ''))])


def parse_choice_line(line):
    """Parse any choice-file version → (path, encoded_name, choice, opts).
    opts always has effort/model/perm/name/worktree/agent/agents_json + cfgdir."""
    t = line.rstrip('\r\n').split('|')
    def g(i):
        v = t[i] if i < len(t) else ''
        return '' if v == '-' else v
    opts = {'effort': '', 'model': '', 'perm': '', 'name': '',
            'worktree': '', 'agent': '', 'agents_json': '', 'cfgdir': ''}
    if t and t[0] == 'v5':
        path, enc, choice = g(1), g(2), g(3)
        opts.update(effort=g(4), model=g(5), perm=g(6), name=g(7),
                    worktree=g(8), cfgdir=g(9), agent=g(10), agents_json=g(11))
    elif t and t[0] == 'v4':
        path, enc, choice = g(1), g(2), g(3)
        opts.update(effort=g(4), model=g(5), perm=g(6), name=g(7),
                    worktree=g(8), cfgdir=g(9), agent=g(10))
    elif t and t[0] == 'v3':
        path, enc, choice = g(1), g(2), g(3)
        opts.update(effort=g(4), model=g(5), perm=g(6), name=g(7),
                    worktree=g(8), cfgdir=g(9))
    elif t and t[0] == 'v2':
        path, enc, choice = g(1), g(2), g(3)
        opts.update(effort=g(4), model=g(5), perm=g(6), name=g(7), worktree=g(8))
    else:   # legacy 5-field: path|enc|action|effort|model
        path, enc, choice = g(0), g(1), g(2)
        opts.update(effort=g(3), model=g(4))
    return path, enc, choice, opts


def launch_from_choice():
    """Read the choice file and launch claude (invoked by the bat as --launch)."""
    try:
        with open(choice_file, 'r', encoding='utf-8') as f:
            line = f.read()
    except Exception:
        return
    try:
        os.remove(choice_file)
    except Exception:
        pass
    path, enc, choice, opts = parse_choice_line(line)
    _direct_launch(path, enc, choice, opts)


def _direct_launch(path, encoded_name, choice, opts):
    """Launch claude.exe (or a terminal) directly. Single launch path for
    both the bat (--launch) and pipx/standalone flows."""
    from .sessions import read_extra_paths, load_add_dirs

    render.screen_restore()   # idempotent — console must be clean for claude

    # config dir: from the choice line (bat path) else the module default
    cfgdir = opts.get('cfgdir') or config_dir
    proj_folder = os.path.join(cfgdir, 'projects', encoded_name) if encoded_name else None

    env = os.environ.copy()
    # Pin the account/config dir explicitly — overrides any ambient
    # CLAUDE_CONFIG_DIR claudectl itself may have been launched under.
    env['CLAUDE_CONFIG_DIR'] = cfgdir
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
    if opts.get('agent'):
        args += ['--agent', opts['agent']]
    # Selected library agents are NOT passed inline (--agents JSON overruns the
    # Windows command line). They're copied into <project>/.claude/agents/ by
    # sync_project_agents at selection time, where Claude auto-discovers them.
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

    try:
        from . import workspace
        workspace.update_manifest(path, proj_folder, 'launch', choice=choice,
                                  opts={k: opts.get(k) for k in ('effort', 'model', 'perm')})
    except Exception:
        pass

    _cls()
    print(f"  Location: {path}")
    print(f"  Action:   {choice}")
    print(f"  {'-' * 42}\n")
    import time as _time
    _launch_t = _time.time()
    try:
        subprocess.call(args, cwd=path, env=env)
    except Exception as e:
        print(f"\n  ✘ Launch failed: {e}")
        pause("\n  Press Enter to exit...")
        sys.exit(1)

    # context-loss insurance: log what this session did (goal + files touched)
    # so the next session can recall it even after /compact
    try:
        from . import health
        if proj_folder and os.path.isdir(proj_folder):
            newest = max((f for f in os.listdir(proj_folder) if f.endswith('.jsonl')),
                         key=lambda f: os.path.getmtime(os.path.join(proj_folder, f)),
                         default=None)
            if newest and os.path.getmtime(os.path.join(proj_folder, newest)) >= _launch_t:
                health.append_session_log(path, proj_folder, newest[:-6])
    except Exception:
        pass
