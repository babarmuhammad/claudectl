"""Memory hub — the single home for everything memory.

One screen shows the state of the whole memory system (entities, lessons,
freshness, what each surface costs in tokens) and hosts every action: build,
ask, preview, lessons review, rules/hook toggles, graph, memory map. Replaces
the old split between ⇧M (memory-map) and n→m (semantic build) that users
conflated.
"""

import os

from . import memory
from . import render
from .config import C_DIM, C_RESET, C_OK, C_WARN


def _newest(paths):
    """Newest mtime among `paths`, or None when none of them exist."""
    best = None
    for p in paths:
        try:
            t = os.path.getmtime(p)
        except OSError:
            continue
        if best is None or t > best:
            best = t
    return best


def last_written(project_path, proj_folder):
    """When each machine-written artifact was last written, as epoch seconds.

    "Is this stale?" is the second question a reader asks after "who wrote
    this?", and the memory tab could not answer it for a single row: the graph
    carries ONE `generated_at`, while the worklog, the rule files and the two
    logs are each written on their own schedule by a different code path.

    The answer is the file's own mtime rather than a stamp inside it. A stamp
    is a second thing to keep in step with the write — one `save()` that
    forgets it and the row lies confidently — whereas an mtime cannot disagree
    with what is on disk. Only artifacts that ARE exactly one file appear here;
    the CLAUDE.md blocks share a file with your own prose, so dating them off
    that file would call the digest fresh because you fixed a typo. Those rows
    stay bare and the build time is stated once, on the group.
    """
    from . import recall as _recall
    from .diffview import _candidate_dirs as _snap_dirs
    from .workspace import _candidate_paths as _manifest_paths
    from .worklog import worklog_path
    rules_dir = os.path.join(project_path or '', '.claude', 'rules')
    try:
        rules = [os.path.join(rules_dir, n) for n in os.listdir(rules_dir)
                 if n.startswith('claudectl-mem-')]
    except OSError:
        rules = []
    snaps = []
    for d in _snap_dirs(project_path, proj_folder):
        try:
            snaps += [os.path.join(d, n) for n in os.listdir(d)]
        except OSError:
            pass
    out = {
        'graph': _newest(os.path.join(d, memory.GRAPH_NAME)
                         for d in memory._mem_dirs(project_path, proj_folder)),
        'rules': _newest(rules),
        'worklog': _newest([worklog_path(project_path)]) if project_path else None,
        'hits': _newest([_recall.hits_log_path(project_path, proj_folder)]),
        'dirty': _newest([memory.dirty_log_path(project_path)]) if project_path else None,
        'manifest': _newest(_manifest_paths(project_path, proj_folder)),
        'snapshots': _newest(snaps),
    }
    return {k: v for k, v in out.items() if v}


def _state(project_path, proj_folder):
    from .config import load_settings
    from .paths import encode_component
    from . import recall
    s = load_settings()
    mem = memory.load_memory(project_path, proj_folder)
    ents = [e for e in mem.get('entities', []) if e.get('type') != 'lesson']
    lessons = [e for e in mem.get('entities', []) if e.get('type') == 'lesson']
    enc = encode_component(os.path.abspath(project_path))
    proj = (s.get('project_defaults') or {}).get(enc) or {}
    hook_on = proj.get('memory_hook', s.get('memory_prompt_hook', False))
    est = recall.estimate_surfaces(project_path, proj_folder, s)
    return {'mem': mem, 'entities': ents, 'lessons': lessons,
            'pending': [l for l in lessons if l.get('status') == 'pending'],
            'hook_on': bool(hook_on), 'rules_on': bool(s.get('memory_rules', True)),
            'auto_on': memory.auto_enabled(project_path, enc),
            'est': est, 'settings': s, 'enc': enc}


def hub_screen(project_path, proj_folder, project_name):
    from .ui import wait_event, flash, text_input, pager
    while True:
        st = _state(project_path, proj_folder)
        mem, est = st['mem'], st['est']
        gen = mem.get('generated_at', '')

        frame = [render.header('CLAUDECTL', project_name, 'MEMORY'), '']
        if st['entities']:
            frame.append(f"  {C_OK}● {len(st['entities'])} entities{C_RESET}"
                         f"  {C_DIM}· {len(mem.get('relations', []))} relations"
                         f" · {len(mem.get('module_edges', []))} module links"
                         f" · built {gen[:10] or '?'}{C_RESET}")
        else:
            frame.append(f"  {C_WARN}● no memory yet{C_RESET}  "
                         f"{C_DIM}press b to build it with Claude{C_RESET}")
        n_l = len(st['lessons'])
        n_p = len(st['pending'])
        if n_l or n_p:
            col = C_WARN if n_p else C_OK
            frame.append(f"  {col}● {n_l} lessons{C_RESET}"
                         + (f"  {C_WARN}({n_p} pending review — press L){C_RESET}" if n_p else ''))
        if mem.get('pending_units'):
            # a capped cycle now does what it can and leaves the rest here, so
            # this is "still queued", not "give up and change a setting"
            frame.append(f"  {C_WARN}● {mem['pending_units']} module(s) still queued{C_RESET}"
                         f"  {C_DIM}(the next cycle takes them, or press b now){C_RESET}")
        if mem.get('last_cost_usd'):
            frame.append(f"  {C_DIM}● last cycle: {mem.get('last_extracted', 0)} module(s), "
                         f"${mem['last_cost_usd']:.3f}{C_RESET}")
        frame += ['', render.hline(), '',
                  f"  {C_DIM}What Claude sees:{C_RESET}",
                  f"    always      CLAUDE.md index          ~{est['digest_tokens']} tok",
                  f"    lazy        {len(est['rules'])} path-scoped rules     load on touch"
                  f"  [{'on' if st['rules_on'] else 'OFF'}]",
                  f"    per prompt  recall hook              "
                  + (f"<={st['settings'].get('memory_budget', 600)} tok"
                     if st['hook_on'] else 'off')
                  + f"  [{'on' if st['hook_on'] else 'OFF'}]",
                  '',
                  # the flag was GUI-only and read by the GUI scheduler alone,
                  # so there was no way to see or set it from here at all
                  f"  {C_DIM}Keep updated in the background:{C_RESET} "
                  + (f"{C_OK}on{C_RESET}" if st['auto_on'] else f"{C_DIM}off{C_RESET}")
                  + (f"  {C_DIM}(last run {mem.get('auto_updated', '')[:16]}){C_RESET}"
                     if st['auto_on'] and mem.get('auto_updated') else ''),
                  '', render.hline(), '',
                  render.hint_keys([('b', 'build/refresh'), ('a', 'ask project'),
                                    ('p', 'preview injection'), ('⇧L', 'lessons')]),
                  render.hint_keys([('s', 'suggestions'), ('d', 'since last session'),
                                    ('h', 'hook on/off'), ('u', 'rules on/off'),
                                    ('o', 'auto on/off'), ('⇧W', 'recent-work'),
                                    ('g', 'open graph'),
                                    ('⇧M', 'memory files'), ('ESC', 'back')])]
        render.render_frame(frame)
        ev = wait_event()
        if ev[0] == 'esc':
            return
        if ev[0] != 'char':
            continue
        ch = ev[1]
        if ch == 'b':
            try:
                mem2 = memory.refresh_memory(project_path, proj_folder, project_name)
                n = len([e for e in mem2.get('entities', []) if e.get('type') != 'lesson'])
                flash(f"Memory built: {n} entities", ok=bool(n), secs=1.5)
            except Exception as e:
                flash(f"Build failed: {e}", ok=False, secs=2)
        elif ch == 'a':
            q = text_input("Ask about this project:")
            if q:
                try:
                    ans = memory.ask_memory(project_path, proj_folder, q)
                except Exception as e:
                    ans = f"(failed: {e})"
                pager(('CLAUDECTL', project_name, 'ASK'),
                      (ans or '(no answer)').splitlines(), hint='ESC back')
        elif ch == 'p':
            from . import recall
            recall.preview_screen(project_path, proj_folder, project_name)
        elif ch == 's':
            from . import brief
            sug = brief.work_suggestions(project_path, proj_folder)
            body = [f"[{tag}] {text}" for tag, text in sug]
            pager(('CLAUDECTL', project_name, 'SUGGESTIONS'), body, hint='ESC back')
        elif ch == 'd':
            from . import brief
            body = brief.session_diff(project_path, proj_folder)
            pager(('CLAUDECTL', project_name, 'SINCE LAST SESSION'), body, hint='ESC back')
        elif ch == 'L':
            from . import lessons as lessons_mod
            pend = lessons_mod.pending_sids(proj_folder, st['mem'])
            if pend:
                lessons_mod.scan_sessions(project_path, proj_folder, pend)
            lessons_mod.review_screen(project_path, proj_folder, project_name)
        elif ch == 'h':
            _toggle_hook(project_path, st, flash)
        elif ch == 'o':
            _toggle_auto(project_path, st, flash)
        elif ch == 'W':
            _toggle_worklog(project_path, st, flash)
        elif ch == 'u':
            on = set_memory_rules(not st['rules_on'], project_path, proj_folder,
                                  st['mem'] if st['entities'] else None)
            flash(f"Path-scoped rules {'enabled' if on else 'disabled'}",
                  ok=on, secs=1.4)
        elif ch == 'g':
            from . import connections
            g = connections.build_hierarchy(project_path, proj_folder)
            try:
                mem = connections.build_memory_hierarchy(project_path, proj_folder)
            except Exception:
                mem = None
            p = connections.write_graph_html(g, project_path, proj_folder,
                                             memory=mem, default_view='memory')
            if not p:
                flash("Could not write graph HTML (check disk/permissions)", ok=False, secs=2.5)
            else:
                ok, err = connections.open_graph(p)
                flash(f"Opened {p}" if ok else f"Could not open graph: {err}",
                      ok=ok, secs=1.2 if ok else 2.5)
        elif ch == 'M':
            from .claude_md import memory_map_menu
            memory_map_menu(project_path, project_name)


# ── the three memory flags: ONE writer each ──────────────────
# The toggle is stored PER PROJECT while the hook that serves it is installed
# PER ACCOUNT, so installing into only the active account made the toggle
# promise something the other accounts could not deliver: the same project
# opened under another login reported the feature on while nothing ran. Every
# install below therefore fans out across accounts.

def set_prompt_hook(enc, on):
    """Per-project recall hook flag. Returns the resulting state."""
    from .config import load_settings, save_settings
    from . import hooks as hooks_mod
    s = load_settings()
    s.setdefault('project_defaults', {}).setdefault(enc, {})['memory_hook'] = bool(on)
    save_settings(s)
    if on:
        hooks_mod.across_accounts(hooks_mod.install_memory_hook)
    return bool(on)


def set_worklog(enc, on):
    """Per-project recent-work (claude-mem style) flag. Returns the state."""
    from .config import load_settings, save_settings
    from . import hooks as hooks_mod
    s = load_settings()
    s.setdefault('project_defaults', {}).setdefault(enc, {})['worklog'] = bool(on)
    save_settings(s)
    if on:
        hooks_mod.across_accounts(hooks_mod.install_worklog_hook)
    return bool(on)


def set_memory_rules(on, project_path=None, proj_folder=None, mem=None):
    """Global path-scoped-rules flag. Turning it on also syncs the rules, which
    is what makes the setting visible in the very next session."""
    from .config import load_settings, save_settings
    s = load_settings()
    s['memory_rules'] = bool(on)
    save_settings(s)
    if on and project_path and proj_folder:
        from .memrules import sync_rules
        sync_rules(project_path, proj_folder,
                   mem if mem is not None else memory.load_memory(project_path, proj_folder))
    return bool(on)


def _toggle_hook(project_path, st, flash):
    new_state = set_prompt_hook(st['enc'], not st['hook_on'])
    flash(f"Per-prompt hook {'ENABLED' if new_state else 'disabled'} for this project",
          ok=new_state, secs=1.6)


def set_auto_memory(enc, on):
    """Per-project background auto-memory. The SAME flag the GUI checkbox
    writes and the GUI/TUI schedulers and the detached worker all read."""
    from .config import load_settings, save_settings
    s = load_settings()
    s.setdefault('project_defaults', {}).setdefault(enc, {})['auto_memory'] = bool(on)
    save_settings(s)
    return bool(on)


def _toggle_auto(project_path, st, flash):
    new_state = set_auto_memory(st['enc'], not st['auto_on'])
    flash('Background memory updates '
          + ('ENABLED for this project' if new_state else 'disabled for this project'),
          ok=new_state, secs=1.6)


def _toggle_worklog(project_path, st, flash):
    """Toggle recent-work memory (claude-mem style) for this project."""
    from .config import load_settings
    proj = (load_settings().get('project_defaults') or {}).get(st['enc']) or {}
    new_state = set_worklog(st['enc'], not proj.get('worklog', False))
    flash(f"Recent-work memory {'ENABLED' if new_state else 'disabled'} for this project",
          ok=new_state, secs=1.6)
