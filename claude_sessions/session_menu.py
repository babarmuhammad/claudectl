import os
import shutil
from datetime import datetime

from .config import W, C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD, C_NAME
from .sessions import load_name, save_name, format_age, get_session_title, scan_sessions
from .ui import text_input, paths_menu, _cls, wait_event, help_screen, flash, menu
from . import render
from .claude_md import scaffold_claude_md, ai_scaffold_claude_md
from .system_prompt import edit_system_prompt


def _sid_of(val):
    """Extract session id from a row value, or None for non-session rows."""
    if not val:
        return None
    if val.startswith('resume-named::'):
        return val[14:].split('::', 1)[0]
    if val.startswith('resume:'):
        return val[7:]
    return None


def _delete_session(folder, sid):
    """Remove session files. Returns list of error strings (empty = ok)."""
    errors = []
    for fname in [f"{sid}.name", f"{sid}.jsonl"]:
        fp = os.path.join(folder, fname)
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception as e:
                errors.append(f"{fname}: {e}")
    sid_dir = os.path.join(folder, sid)
    if os.path.isdir(sid_dir):
        try:
            shutil.rmtree(sid_dir)
        except Exception as e:
            errors.append(f"{sid}/: {e}")
    return errors


def _move_session(src_folder, dst_folder, sid):
    """Move session files between live and archived folders.
    Moves the .jsonl LAST so a partial failure leaves the session resumable.
    Returns list of error strings."""
    errors = []
    try:
        os.makedirs(dst_folder, exist_ok=True)
    except Exception as e:
        return [str(e)]
    moves = []
    name_f = os.path.join(src_folder, f"{sid}.name")
    if os.path.exists(name_f):
        moves.append((name_f, os.path.join(dst_folder, f"{sid}.name")))
    sid_dir = os.path.join(src_folder, sid)
    if os.path.isdir(sid_dir):
        moves.append((sid_dir, os.path.join(dst_folder, sid)))
    moves.append((os.path.join(src_folder, f"{sid}.jsonl"),
                  os.path.join(dst_folder, f"{sid}.jsonl")))   # jsonl last
    for src, dst in moves:
        try:
            shutil.move(src, dst)
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")
            break
    return errors


# ── sessions menu ────────────────────────────────────────────

def sessions_menu(sessions_in, proj_folder, project_name, project_path):
    sessions       = list(sessions_in)   # (mtime, sid, preview, count)
    archived_dir   = os.path.join(proj_folder, 'archived') if proj_folder else None
    show_archived  = False
    arch_sessions  = None                # lazy scan
    filter_str     = ''
    search_focused = False   # True = cursor on search bar, typing goes there
    nav_pos        = 0       # index into nav_indices of current list item

    # Display name: manual rename wins, else AI-generated transcript title.
    # Cache key includes folder — the same sid can exist in both the live
    # and archived folders, and must not share a cached name.
    names = {}

    def _name_of(folder, sid):
        key = (folder, sid)
        if key not in names:
            names[key] = load_name(folder, sid) or \
                         get_session_title(os.path.join(folder, f"{sid}.jsonl"))
        return names[key]

    def active_sessions():
        src = arch_sessions if show_archived else sessions
        if not filter_str:
            return src
        fl = filter_str.lower()
        folder = archived_dir if show_archived else proj_folder
        return [s for s in src if fl in (_name_of(folder, s[1]) + s[2]).lower()]

    def build_rows(active):
        folder = archived_dir if show_archived else proj_folder
        if show_archived:
            rows = [(f"{'─' * W}", None)]
            if not active:
                rows.append((f"{C_DIM}(no archived sessions){C_RESET}", None))
        else:
            rows = [(f"{'─' * W}", None), (f"+ New Chat", 'new')]
            if sessions:
                rows.append((f"+ Continue latest  {C_DIM}(claude -c){C_RESET}", 'continue'))
            rows.append((f"{'─' * W}", None))
        for i, (mtime, sid, preview, count) in enumerate(active, 1):
            age  = format_age(mtime).strip()
            date = datetime.fromtimestamp(mtime).strftime('%d %b %Y')
            name = _name_of(folder, sid)
            badge = f"{C_DIM}[{count}]{C_RESET} " if count else ''
            if name:
                disp = f"{C_NAME}{render.trunc(name, 30)}{C_RESET}  {C_DIM}{preview if preview else date}{C_RESET}"
            elif preview:
                disp = f"{badge}{preview}"
            else:
                disp = f"{badge}{C_DIM}(no preview — {date}){C_RESET}"
            val = f"resume-named::{sid}::{name}" if name else f"resume:{sid}"
            label = render.cols(
                [f"{C_DIM}#{i}{C_RESET}", f"{C_DIM}{age}{C_RESET}", disp],
                [5, 7, None])
            rows.append((label, val))
        if not show_archived:
            rows += [(f"{'─' * W}", None), (f"{C_DIM}Terminal only{C_RESET}", 'terminal')]
        return rows

    while True:
        if show_archived and arch_sessions is None:
            arch_sessions = scan_sessions(archived_dir)

        active      = active_sessions()
        rows        = build_rows(active)
        nav_indices = [i for i, (_, v) in enumerate(rows) if v is not None]
        if not nav_indices:
            # archived view can be empty — fall back to a dummy nav target
            rows.append((f"{C_DIM}Back (ESC){C_RESET}", '__noop__'))
            nav_indices = [len(rows) - 1]
        if nav_pos >= len(nav_indices):
            nav_pos = 0

        crumb = 'ARCHIVED' if show_archived else 'SESSIONS'
        frame = [render.header('CLAUDECTL', project_name, crumb), '']

        # Search bar — always visible; focused = cursor + blinking input indicator
        if search_focused:
            frame.append(f"  {C_SEL}▸{C_RESET} {C_SRCH}[ {filter_str}▌ ]{C_RESET}")
        elif filter_str:
            frame.append(f"    {C_SRCH}[ {filter_str} ]{C_RESET}  {C_DIM}(↑ to edit, ESC to clear){C_RESET}")
        else:
            frame.append(f"    {C_DIM}[ search... ]{C_RESET}  {C_DIM}(↑ from top to search){C_RESET}")
        frame.append('')

        cur = nav_indices[nav_pos]
        # window rows so hints always fit the terminal height.
        # chrome below the list = blank(1) + hint lines (2 only in the
        # full non-focused non-archived state, else 1)
        hint_n = 2 if (not search_focused and not show_archived) else 1
        fixed = 2 + 2 + 1 + hint_n   # header+blank, search+blank, blank, hints
        avail = max(3, render.frame_height() - fixed)
        n = len(rows)
        start, end = 0, n
        if n > avail:
            vis = max(1, avail - 2)
            start = min(max(cur - vis // 2, 0), n - vis)
            end = start + vis
        if start > 0:
            frame.append(f"  {C_DIM}… {start} more ↑{C_RESET}")
        for i in range(start, end):
            label, val = rows[i]
            if val is None:
                frame.append(render.sep_line(label))
            else:
                frame.append(render.row(label, selected=(i == cur and not search_focused)))
        if end < n:
            frame.append(f"  {C_DIM}… {n - end} more ↓{C_RESET}")

        frame.append('')
        if search_focused:
            frame.append(render.hint_bar("type to search   ↓/ENTER go to list   ESC clear / exit"))
        elif show_archived:
            frame.append(render.hint_bar("d restore/delete   A back to sessions   ESC back"))
        else:
            frame.append(render.hint_bar(
                "r rename  d archive/delete  f fork  v view  e export  i info  u usage"))
            frame.append(render.hint_bar(
                "p paths  x add-dirs  c claude.md  a ai-analyze  s sys-prompt  A archived  ? help"))
        render.render_frame(frame)

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

        cur_val = rows[cur][1]
        cur_sid = _sid_of(cur_val)
        folder  = archived_dir if show_archived else proj_folder

        # ── list focused ──────────────────────────────────────
        if ev[0] == 'up':
            if nav_pos == 0:
                search_focused = True   # go to search bar
            else:
                nav_pos -= 1

        elif ev[0] == 'down':
            nav_pos = min(nav_pos + 1, len(nav_indices) - 1)

        elif ev[0] == 'enter':
            if cur_val == '__noop__':
                continue
            if show_archived:
                flash("Archived — press d to restore first", ok=False, secs=1.2)
                continue
            return cur_val

        elif ev[0] == 'esc':
            if filter_str:
                filter_str = ''
                nav_pos    = 0
            elif show_archived:
                show_archived = False
                nav_pos = 0
            else:
                return None

        elif ev[0] == 'back':   # BACKSPACE — shortcut: focus search and delete
            if filter_str:
                filter_str     = filter_str[:-1]
                search_focused = True
                nav_pos        = 0

        elif ev[0] == 'char' and ev[1] == 'A':
            show_archived = not show_archived
            arch_sessions = None   # rescan on entry
            filter_str = ''
            nav_pos = 0

        elif ev[0] == 'char' and ev[1] == 'r' and cur_sid and not show_archived:
            new_name = text_input("Rename session:",
                                  default=names.get((proj_folder, cur_sid), ''))
            if new_name is not None:
                names[(proj_folder, cur_sid)] = new_name
                try:
                    save_name(proj_folder, cur_sid, new_name)
                    flash(f"Renamed to '{new_name}'" if new_name else "Name cleared")
                except Exception as e:
                    flash(f"Rename failed: {e}", ok=False, secs=1.5)

        elif ev[0] == 'char' and ev[1] == 'd' and cur_sid:
            label = names.get((folder, cur_sid)) or cur_sid[:8]
            if show_archived:
                act = menu([('Restore  (move back to sessions)', 'restore'),
                            ('Delete permanently', 'delete'),
                            ('Cancel', 'cancel')],
                           f"ARCHIVED SESSION  {label}")
                if act == 'restore':
                    errors = _move_session(archived_dir, proj_folder, cur_sid)
                    if errors:
                        flash("Restore failed: " + "; ".join(errors)[:120], ok=False, secs=2)
                    else:
                        flash("Session restored")
                        sessions = scan_sessions(proj_folder)
                        arch_sessions = None
                elif act == 'delete':
                    confirm = text_input("Delete permanently? Type 'yes':")
                    if confirm and confirm.lower() == 'yes':
                        errors = _delete_session(archived_dir, cur_sid)
                        if errors:
                            flash("Delete failed: " + "; ".join(errors)[:120], ok=False, secs=2)
                        else:
                            flash("Session deleted")
                            arch_sessions = None
            else:
                act = menu([('Archive  (move to archived/, restorable)', 'archive'),
                            ('Delete permanently', 'delete'),
                            ('Cancel', 'cancel')],
                           f"SESSION  {label}")
                if act == 'archive':
                    errors = _move_session(proj_folder, archived_dir, cur_sid)
                    if errors:
                        flash("Archive failed: " + "; ".join(errors)[:120], ok=False, secs=2)
                    else:
                        flash("Session archived")
                        sessions = [s for s in sessions if s[1] != cur_sid]
                        arch_sessions = None
                elif act == 'delete':
                    confirm = text_input("Delete permanently? Type 'yes':")
                    if confirm and confirm.lower() == 'yes':
                        errors = _delete_session(proj_folder, cur_sid)
                        if errors:
                            flash("Delete failed: " + "; ".join(errors)[:120], ok=False, secs=2)
                        else:
                            flash("Session deleted")
                        if not os.path.exists(os.path.join(proj_folder, f"{cur_sid}.jsonl")):
                            sessions = [s for s in sessions if s[1] != cur_sid]
                            names.pop((proj_folder, cur_sid), None)
                # rows shrank by one selectable; loop top also re-clamps
                nav_pos = max(0, min(nav_pos, len(nav_indices) - 2))

        elif ev[0] == 'char' and ev[1] == 'f' and cur_sid and not show_archived:
            return f"fork:{cur_sid}"

        elif ev[0] == 'char' and ev[1] == 'v' and cur_sid:
            from .transcript import view_transcript
            view_transcript(folder, cur_sid, project_name, project_path)

        elif ev[0] == 'char' and ev[1] == 'e' and cur_sid:
            from .transcript import export_transcript
            ok, msg = export_transcript(folder, cur_sid, project_path)
            flash(msg, ok=ok, secs=1.2)

        elif ev[0] == 'char' and ev[1] == 'i' and cur_sid:
            from .transcript import show_metadata
            show_metadata(folder, cur_sid, project_name)

        elif ev[0] == 'char' and ev[1] == 'u' and not show_archived:
            from .stats import project_usage_screen
            project_usage_screen(proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'p' and not show_archived:
            paths_menu(proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'x' and not show_archived:
            paths_menu(proj_folder, project_name,
                       filename='add-dirs.txt', title='ADD DIRS (--add-dir)')

        elif ev[0] == 'char' and ev[1] == 'c' and not show_archived:
            scaffold_claude_md(project_path, proj_folder)

        elif ev[0] == 'char' and ev[1] == 'a' and not show_archived:
            ai_scaffold_claude_md(project_path, proj_folder)

        elif ev[0] == 'char' and ev[1] == 's' and not show_archived:
            edit_system_prompt(proj_folder, project_name, project_path)

        elif ev[0] == 'char' and ev[1] == '?':
            help_screen()
