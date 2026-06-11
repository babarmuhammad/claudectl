import os
import shutil
from datetime import datetime

from .config import W, C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD
from .sessions import load_name, save_name, format_age, get_session_info
from .ui import text_input, paths_menu, _cls, wait_event
from .claude_md import scaffold_claude_md, ai_scaffold_claude_md
from .system_prompt import edit_system_prompt


# ── sessions menu ────────────────────────────────────────────

def sessions_menu(sessions_in, proj_folder, project_name, project_path):
    sessions       = list(sessions_in)   # (mtime, sid, preview, count)
    names          = {sid: load_name(proj_folder, sid) for _, sid, _, _ in sessions}
    filter_str     = ''
    search_focused = False   # True = cursor on search bar, typing goes there
    nav_pos        = 0       # index into nav_indices of current list item

    def active_sessions():
        if not filter_str:
            return sessions
        fl = filter_str.lower()
        return [s for s in sessions if fl in (names.get(s[1], '') + s[2]).lower()]

    def build_rows(active):
        rows = [(f"{'─' * W}", None), (f"  + New Chat", 'new'), (f"{'─' * W}", None)]
        for i, (mtime, sid, preview, count) in enumerate(active, 1):
            age  = format_age(mtime)
            date = datetime.fromtimestamp(mtime).strftime('%d %b %Y')
            name = names.get(sid, '')
            badge = f"[{count}] " if count else ''
            if name:
                disp = f"\033[97m{name}\033[0m  \033[90m{preview[:35] if preview else date}\033[0m"
            elif preview:
                disp = f"{badge}{preview}"
            else:
                disp = f"{badge}{C_DIM}(no preview — {date}){C_RESET}"
            val = f"resume-named::{sid}::{name}" if name else f"resume:{sid}"
            rows.append((f"  {C_DIM}#{i:<2}  {age}{C_RESET}  {disp}", val))
        rows += [(f"{'─' * W}", None), (f"  {C_DIM}Terminal only{C_RESET}", 'terminal')]
        return rows

    while True:
        active      = active_sessions()
        rows        = build_rows(active)
        nav_indices = [i for i, (_, v) in enumerate(rows) if v is not None]
        if nav_pos >= len(nav_indices):
            nav_pos = 0

        _cls()
        print(f"\n  {C_TITLE}{C_BOLD}SESSIONS  /  {project_name}{C_RESET}\n")

        # Search bar — always visible; focused = cursor + blinking input indicator
        if search_focused:
            print(f"  {C_SEL}>{C_RESET} {C_SRCH}[ {filter_str}▌ ]{C_RESET}\n")
        elif filter_str:
            print(f"    {C_SRCH}[ {filter_str} ]{C_RESET}  {C_DIM}(↑ to edit, ESC to clear){C_RESET}\n")
        else:
            print(f"    {C_DIM}[ search... ]{C_RESET}  {C_DIM}(↑ from top to search){C_RESET}\n")

        cur = nav_indices[nav_pos]
        for i, (label, val) in enumerate(rows):
            if val is None:
                print(f"  {C_DIM}{label}{C_RESET}")
            elif i == cur and not search_focused:
                print(f"  {C_SEL}>{C_RESET}{label}")
            else:
                print(f"   {label}")

        if search_focused:
            print(f"\n  {C_DIM}type to search   ↓/ENTER go to list   ESC clear / exit{C_RESET}")
        else:
            print(f"\n  {C_DIM}r rename  d delete  f fork  p paths  c claude.md  a ai-analyze  s sys-prompt{C_RESET}")
            print(f"  {C_DIM}↑↓ navigate   ENTER select   ESC back   ↑ from top → search{C_RESET}")

        ev = wait_event()

        # ── search bar focused ────────────────────────────────
        if search_focused:
            if ev[0] in ('down', 'enter'):
                search_focused = False
            elif ev[0] == 'esc':
                if filter_str:
                    filter_str = ''
                    nav_pos    = 0
                else:
                    search_focused = False
            elif ev[0] == 'back':
                if filter_str:
                    filter_str = filter_str[:-1]
                    nav_pos    = 0
                else:
                    search_focused = False
            elif ev[0] == 'char':
                filter_str += ev[1]
                nav_pos     = 0
            continue

        # ── list focused ──────────────────────────────────────
        if ev[0] == 'up':
            if nav_pos == 0:
                search_focused = True   # go to search bar
            else:
                nav_pos -= 1

        elif ev[0] == 'down':
            nav_pos = min(nav_pos + 1, len(nav_indices) - 1)

        elif ev[0] == 'enter':
            return rows[cur][1]

        elif ev[0] == 'esc':
            if filter_str:
                filter_str = ''
                nav_pos    = 0
            else:
                return None

        elif ev[0] == 'back':   # BACKSPACE — shortcut: focus search and delete
            if filter_str:
                filter_str     = filter_str[:-1]
                search_focused = True
                nav_pos        = 0

        elif ev[0] == 'char' and ev[1] == 'r':
            val = rows[cur][1]
            if val and (val.startswith('resume:') or val.startswith('resume-named::')):
                sid = val.split('::')[1] if '::' in val else val[7:]
                new_name = text_input("Rename session:", default=names.get(sid, ''))
                if new_name is not None:
                    names[sid] = new_name
                    save_name(proj_folder, sid, new_name)

        elif ev[0] == 'char' and ev[1] == 'd':
            val = rows[cur][1]
            if val and (val.startswith('resume:') or val.startswith('resume-named::')):
                sid = val.split('::')[1] if '::' in val else val[7:]
                confirm = text_input("Delete session? Type 'yes' to confirm:")
                if confirm and confirm.lower() == 'yes':
                    for fname in [f"{sid}.jsonl", f"{sid}.name"]:
                        fp = os.path.join(proj_folder, fname)
                        if os.path.exists(fp):
                            try: os.remove(fp)
                            except Exception: pass
                    sid_dir = os.path.join(proj_folder, sid)
                    if os.path.isdir(sid_dir):
                        try: shutil.rmtree(sid_dir)
                        except Exception: pass
                    sessions = [s for s in sessions if s[1] != sid]
                    if sid in names: del names[sid]
                    nav_pos = min(nav_pos, max(0, len(nav_indices) - 2))

        elif ev[0] == 'char' and ev[1] == 'f':
            val = rows[cur][1]
            if val and (val.startswith('resume:') or val.startswith('resume-named::')):
                sid = val.split('::')[1] if '::' in val else val[7:]
                return f"fork:{sid}"

        elif ev[0] == 'char' and ev[1] == 'p':
            paths_menu(proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'c':
            scaffold_claude_md(project_path, proj_folder)

        elif ev[0] == 'char' and ev[1] == 'a':
            ai_scaffold_claude_md(project_path, proj_folder)

        elif ev[0] == 'char' and ev[1] == 's':
            edit_system_prompt(proj_folder, project_name, project_path)
