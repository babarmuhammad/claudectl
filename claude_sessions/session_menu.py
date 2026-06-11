import os
import msvcrt
import shutil
from datetime import datetime

from .config import W
from .sessions import load_name, save_name, format_age, get_session_info
from .ui import text_input, paths_menu
from .claude_md import scaffold_claude_md, ai_scaffold_claude_md
from .system_prompt import edit_system_prompt


# ── sessions menu ────────────────────────────────────────────

def sessions_menu(sessions_in, proj_folder, project_name, project_path):
    sessions = list(sessions_in)  # (mtime, sid, preview, count)
    names = {sid: load_name(proj_folder, sid) for _, sid, _, _ in sessions}
    filter_str = ''

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
                disp = f"{badge}(no preview — {date})"
            val = f"resume-named::{sid}::{name}" if name else f"resume:{sid}"
            rows.append((f"  #{i:<2}  {age}  {disp}", val))
        rows += [(f"{'─' * W}", None), (f"  Terminal only", 'terminal')]
        return rows

    nav_pos = 0

    while True:
        active = active_sessions()
        rows   = build_rows(active)
        nav_indices = [i for i, (_, v) in enumerate(rows) if v is not None]
        if nav_pos >= len(nav_indices):
            nav_pos = 0
        cur = nav_indices[nav_pos]

        filter_tag = f"  [▸ {filter_str}]" if filter_str else ''
        os.system('cls')
        print(f"\n  SESSIONS  /  {project_name}{filter_tag}\n")
        for i, (label, val) in enumerate(rows):
            print(f"  {label}" if val is None else f"  {'>' if i == cur else ' '} {label}")
        print(f"\n  R rename  D delete  F fork  P paths  C claude.md  A ai-analyze  S sys-prompt")
        print(f"  type to filter   UP/DOWN navigate   ENTER select   ESC back")

        key = ord(msvcrt.getch())

        if key == 224:
            k2 = ord(msvcrt.getch())
            if k2 == 72:   nav_pos = (nav_pos - 1) % len(nav_indices)
            elif k2 == 80: nav_pos = (nav_pos + 1) % len(nav_indices)

        elif key == 13:
            return rows[cur][1]

        elif key == 27:
            if filter_str:
                filter_str = ''
            else:
                return None

        elif key == 8:
            if filter_str:
                filter_str = filter_str[:-1]

        elif key in (ord('r'), ord('R')):
            val = rows[cur][1]
            if val and (val.startswith('resume:') or val.startswith('resume-named::')):
                sid = val.split('::')[1] if '::' in val else val[7:]
                new_name = text_input("Rename session:", default=names.get(sid, ''))
                if new_name is not None:
                    names[sid] = new_name
                    save_name(proj_folder, sid, new_name)

        elif key in (ord('d'), ord('D')):
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

        elif key in (ord('f'), ord('F')):
            val = rows[cur][1]
            if val and (val.startswith('resume:') or val.startswith('resume-named::')):
                sid = val.split('::')[1] if '::' in val else val[7:]
                return f"fork:{sid}"

        elif key in (ord('p'), ord('P')):
            paths_menu(proj_folder, project_name)

        elif key in (ord('c'), ord('C')):
            scaffold_claude_md(project_path, proj_folder)

        elif key in (ord('a'), ord('A')):
            ai_scaffold_claude_md(project_path, proj_folder)

        elif key in (ord('s'), ord('S')):
            edit_system_prompt(proj_folder, project_name, project_path)

        elif 32 <= key <= 126:
            filter_str += chr(key)
            nav_pos = 0
