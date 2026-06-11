import os
import sys

from .config import projects_dir, choice_file, global_claude_md
from .config import C_RESET, C_STAR, C_DIM, C_TITLE, C_BOLD
from .paths import find_actual_path
from .sessions import get_session_info, load_recent_sessions, save_last_session, format_age
from .ui import menu, launch_options_menu, pause
from .session_menu import sessions_menu
from .mcp import mcp_status_line, global_claude_md_menu, mcp_servers
from .ui import _cls


def run():
    # ── UTF-8 console ─────────────────────────────────────────────
    os.system('chcp 65001 >nul 2>&1')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

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
            label = f"{C_STAR}{star}{C_RESET}  {lr_proj:<16}  {lr_preview[:38]}  {C_DIM}({lr_age}){C_RESET}"
            qr_items.append((label, f"__quickresume_{i}__"))
        full_items = qr_items + [(f"{'─' * W}", None)] + project_items
    else:
        full_items = project_items

    full_items = full_items + [(f"{'─' * W}", None), ('⚙  Global CLAUDE.md  /  MCP Analysis', '__global_claude_md__')]

    # ── main loop ─────────────────────────────────────────────────

    path = encoded_name = proj_folder = choice = None
    effort, model = '', ''

    while True:
        sel = menu(full_items, "SELECT PROJECT", footer_fn=mcp_status_line)
        if not sel:
            sys.exit(0)

        if sel and sel.startswith('__quickresume_'):
            idx  = int(sel[len('__quickresume_'):-2])
            sess = recent[idx]
            path         = sess['project_path']
            encoded_name = sess['encoded_name']
            proj_folder  = os.path.join(projects_dir, encoded_name)
            choice       = f"resume:{sess['session_id']}"

        elif sel == '__global_claude_md__':
            global_claude_md_menu()
            continue

        else:
            path = sel
            encoded_name = next((n for _, p, n in entries if p == path), None)
            proj_folder  = os.path.join(projects_dir, encoded_name) if encoded_name else None

            sessions = []
            if proj_folder and os.path.exists(proj_folder):
                for f in os.listdir(proj_folder):
                    if not f.endswith('.jsonl'):
                        continue
                    fpath = os.path.join(proj_folder, f)
                    mtime = os.path.getmtime(fpath)
                    preview, count = get_session_info(fpath)
                    sessions.append((mtime, f[:-6], preview, count))
                sessions.sort(reverse=True)

            project_name = os.path.basename(path) or path
            choice = sessions_menu(sessions, proj_folder, project_name, path)
            if not choice:
                continue

        # Launch options (skip for terminal); ESC = back to main menu
        if choice == 'terminal':
            break
        opts = launch_options_menu(os.path.basename(path) or path)
        if opts is None:
            choice = None
            continue
        effort, model = opts
        break

    # Persist last session for quick-resume (resume/fork only)
    if choice and choice not in ('terminal', 'new'):
        sid = choice.split('::')[1] if '::' in choice else \
              (choice.split(':')[1] if ':' in choice else '')
        if sid:
            save_last_session(path, encoded_name, sid)

    with open(choice_file, 'w', encoding='utf-8') as f:
        f.write(f"{path}|{encoded_name}|{choice}|{effort}|{model}")
