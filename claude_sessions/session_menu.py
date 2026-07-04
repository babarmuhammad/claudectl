import os
import shutil
from datetime import datetime

from .config import W, C_RESET, C_TITLE, C_SEL, C_DIM, C_SRCH, C_BOLD, C_NAME, C_WARN
from .sessions import (load_name, save_name, format_age, get_session_title,
                       scan_sessions, load_tags, save_tags)
from .ui import (text_input, paths_menu, _cls, wait_event, help_screen,
                 flash, menu, confirm)
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
    # session-learning badge (cheap local scan; 'auto' mode extracts on entry)
    unlearned = 0
    try:
        from .config import load_settings as _ls
        from . import lessons as _lessons
        from . import memory as _memory
        _lmode = _ls().get('memory_lessons', 'prompt')
        if _lmode != 'off':
            _mem0 = _memory.load_memory(project_path, proj_folder)
            _pend = _lessons.pending_sids(proj_folder, _mem0)
            if _pend and _lmode == 'auto':
                _lessons.scan_sessions(project_path, proj_folder, _pend)
                _pend = []
            unlearned = len(_pend)
    except Exception:
        unlearned = 0
    # memory auto-refresh on open: incremental, capped so a big rebuild never
    # runs silently; only when memory already exists and a few units changed
    try:
        from .config import load_settings as _ls3
        if _ls3().get('memory_auto_refresh') == 'open' and project_path and proj_folder:
            from . import memory as _memory
            _m0 = _memory.load_memory(project_path, proj_folder)
            if _m0.get('entities'):
                _memory.refresh_memory(project_path, proj_folder, project_name, auto_cap=6)
    except Exception:
        pass
    # agents auto-mode: first open with no explicit selection → apply suggestions
    try:
        from .config import load_settings as _ls2
        if _ls2().get('agents_auto') == 'auto' and proj_folder and project_path:
            from .sessions import load_session_agents, save_session_agents
            if '__project__' not in load_session_agents(proj_folder):
                from .agents import suggest_agents, sync_project_agents
                sug = [ref for ref, _r, _s in suggest_agents(project_path, proj_folder)]
                if sug:
                    save_session_agents(proj_folder, '__project__', sug)
                    sync_project_agents(project_path, sug)
    except Exception:
        pass
    show_archived  = False
    arch_sessions  = None                # lazy scan
    filter_str     = ''
    search_focused = False   # True = cursor on search bar, typing goes there
    nav_pos        = 0       # index into nav_indices of current list item
    pending_ev     = None    # synthesized event (action palette dispatch)
    # first-open setup badge: no CLAUDE.md AND no memory graph
    try:
        not_set_up = (project_path
                      and not os.path.isfile(os.path.join(project_path, 'CLAUDE.md'))
                      and not os.path.isfile(os.path.join(
                          project_path, '.claudectl', 'memory', 'graph.json')))
    except Exception:
        not_set_up = False

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
        tags_map = load_tags(proj_folder) if not show_archived else {}
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
            tg = tags_map.get(sid)
            if tg:
                disp += f"  {C_SRCH}#{' #'.join(tg)}{C_RESET}"
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
        if unlearned and not show_archived:
            frame.append(f"  {C_WARN}● {unlearned} session{'s' if unlearned > 1 else ''} "
                         f"unlearned{C_RESET}  {C_DIM}press L to review/learn{C_RESET}")
        if not_set_up and not show_archived:
            frame.append(f"  {C_WARN}● project not set up{C_RESET}  "
                         f"{C_DIM}press ! for one-key setup (CLAUDE.md + memory + rules){C_RESET}")

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
        hint_n = 3 if (not search_focused and not show_archived) else 1
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
            frame.append(render.hint_keys([('type', 'to search'), ('↓/ENTER', 'go to list'),
                                            ('ESC', 'clear / exit')]))
        elif show_archived:
            frame.append(render.hint_keys([('d', 'restore/delete'), ('⇧A', 'back to sessions'),
                                            ('ESC', 'back')]))
        else:
            frame.append(render.hint_keys([
                ('v', 'view'), ('r', 'rename'), ('f', 'fork'),
                ('t', 'tag'), ('d', 'archive'), ('e', 'export'), ('i', 'info'),
                ('⇧F', 'files'), ('⇧A', 'archived')], prefix='session:'))
            frame.append(render.hint_keys([
                ('m', 'memory'), ('g', 'agents'), ('n', 'graph'), ('⇧X', 'plan→exec'),
                ('a', 'ai-analyze'), ('c', 'claude.md'), ('s', 'sys-prompt'),
                ('u', 'usage'), ('w', 'status')], prefix='project:'))
            frame.append(render.hint_bar(
                f"{C_DIM}/ all actions · ? help · ⇧ = Shift (capital){C_RESET}"))
        render.render_frame(frame)

        if pending_ev is not None:
            ev, pending_ev = pending_ev, None
        else:
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
                    if confirm(f"Delete '{label}' permanently?", danger=True):
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
                    if confirm(f"Delete '{label}' permanently?", danger=True):
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

        elif ev[0] == 'char' and ev[1] == 'F' and cur_sid:
            from .sessions import session_changed_files
            from .ui import pager
            changed = session_changed_files(os.path.join(folder, f"{cur_sid}.jsonl"))
            if changed:
                lines = [f"{render.fit(p, render.content_width() - 8)}  {C_DIM}×{n}{C_RESET}"
                         for p, n in changed]
            else:
                lines = [f"{C_DIM}(no file edits recorded in this session){C_RESET}"]
            pager(('CLAUDECTL', project_name, 'CHANGED FILES'), lines)

        elif ev[0] == 'char' and ev[1] == 'M' and not show_archived:
            from .claude_md import memory_map_menu
            memory_map_menu(project_path, project_name)

        elif ev[0] == 'char' and ev[1] == 't' and cur_sid and not show_archived:
            tags = load_tags(proj_folder)
            cur = ', '.join(tags.get(cur_sid, []))
            v = text_input("Tags (comma-separated):", default=cur)
            if v is not None:
                newtags = [t.strip() for t in v.split(',') if t.strip()]
                if newtags:
                    tags[cur_sid] = newtags
                else:
                    tags.pop(cur_sid, None)
                save_tags(proj_folder, tags)
                flash("Tags saved")

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

        elif ev[0] == 'char' and ev[1] == 'w' and not show_archived:
            from . import workspace
            workspace.workspace_status_screen(project_path, proj_folder)

        elif ev[0] == 'char' and ev[1] == 'n' and not show_archived:
            from . import connections
            connections.connections_screen(project_path, proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'm' and not show_archived:
            from . import memhub
            memhub.hub_screen(project_path, proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'X' and not show_archived:
            from . import plan_execute
            plan_execute.run(project_path, proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == '!' and not show_archived and not_set_up:
            # one-key project setup: CLAUDE.md scaffold → memory (rules sync inside)
            if confirm("Set up project now? (scaffold CLAUDE.md + build memory with Claude)"):
                from .claude_md import scaffold_claude_md
                from . import memory as memory_mod
                try:
                    scaffold_claude_md(project_path, proj_folder)
                    mem = memory_mod.refresh_memory(project_path, proj_folder, project_name)
                    n = len(mem.get('entities', []))
                    flash(f"Project set up: CLAUDE.md + {n} memory entities", ok=True, secs=2)
                    not_set_up = False
                except Exception as e:
                    flash(f"Setup failed: {e}", ok=False, secs=2)

        elif ev[0] == 'char' and ev[1] == '/' and not show_archived:
            # Action palette — every action, discoverable, type-to-filter
            actions = [
                ('Memory hub (build, ask, preview, lessons)', 'm'),
                ('View transcript', 'v'), ('Rename session', 'r'),
                ('Fork session', 'f'), ('Tag session', 't'),
                ('Archive session', 'd'), ('Export to markdown', 'e'),
                ('Session info (tokens, cost)', 'i'), ('Changed files', 'F'),
                ('Archived sessions view', 'A'), ('Lessons review', 'L'),
                ('Plan with one model, execute with another', 'X'),
                ('Architecture graph', 'n'), ('Project agents', 'g'),
                ('AI-analyze CLAUDE.md', 'a'), ('Scaffold CLAUDE.md', 'c'),
                ('System prompt', 's'), ('Memory files map', 'M'),
                ('Project usage stats', 'u'), ('Workspace status', 'w'),
                ('Extra PATH entries', 'p'), ('Add directories', 'x'),
                ('Help', '?'),
            ]
            pick = menu(actions, "ACTIONS  (type to filter)")
            if pick:
                pending_ev = ('char', pick)

        elif ev[0] == 'char' and ev[1] == 'L' and not show_archived:
            from . import lessons as lessons_mod
            from . import memory as memory_mod
            if unlearned:
                pend = lessons_mod.pending_sids(
                    proj_folder, memory_mod.load_memory(project_path, proj_folder))
                if pend:
                    added, scanned = lessons_mod.scan_sessions(project_path, proj_folder, pend)
                    unlearned = 0
            lessons_mod.review_screen(project_path, proj_folder, project_name)

        elif ev[0] == 'char' and ev[1] == 'g' and not show_archived:
            from .agents import select_session_agents, sync_project_agents
            from .sessions import load_session_agents, save_session_agents
            pre = load_session_agents(proj_folder).get('__project__', []) if proj_folder else []
            refs = select_session_agents(project_name, pre,
                                         project_path=project_path,
                                         proj_folder=proj_folder)
            if refs is not None:
                if proj_folder:
                    save_session_agents(proj_folder, '__project__', refs)
                n = sync_project_agents(project_path, refs)
                flash(f"{n} project agent(s) active" if refs else "Project agents cleared")

        elif ev[0] == 'char' and ev[1] == '?':
            help_screen()
