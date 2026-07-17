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
            frame.append(f"  {C_WARN}● coverage incomplete: {mem['pending_units']} units "
                         f"pending{C_RESET}  {C_DIM}(raise memory_max_calls, then b){C_RESET}")
        frame += ['', render.hline(), '',
                  f"  {C_DIM}What Claude sees:{C_RESET}",
                  f"    always      CLAUDE.md index          ~{est['digest_tokens']} tok",
                  f"    lazy        {len(est['rules'])} path-scoped rules     load on touch"
                  f"  [{'on' if st['rules_on'] else 'OFF'}]",
                  f"    per prompt  recall hook              "
                  + (f"<={st['settings'].get('memory_budget', 600)} tok"
                     if st['hook_on'] else 'off')
                  + f"  [{'on' if st['hook_on'] else 'OFF'}]",
                  '', render.hline(), '',
                  render.hint_keys([('b', 'build/refresh'), ('a', 'ask project'),
                                    ('p', 'preview injection'), ('⇧L', 'lessons')]),
                  render.hint_keys([('s', 'suggestions'), ('d', 'since last session'),
                                    ('h', 'hook on/off'), ('u', 'rules on/off'),
                                    ('g', 'open graph'), ('⇧M', 'memory files'),
                                    ('ESC', 'back')])]
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
        elif ch == 'u':
            from .config import load_settings, save_settings
            s = load_settings()
            s['memory_rules'] = not s.get('memory_rules', True)
            save_settings(s)
            if s['memory_rules'] and st['entities']:
                from .memrules import sync_rules
                sync_rules(project_path, proj_folder, st['mem'])
            flash(f"Path-scoped rules {'enabled' if s['memory_rules'] else 'disabled'}",
                  ok=s['memory_rules'], secs=1.4)
        elif ch == 'g':
            from . import connections
            g = connections.build_hierarchy(project_path, proj_folder)
            p = connections.write_graph_html(g, project_path, proj_folder)
            if not p:
                flash("Could not write graph HTML (check disk/permissions)", ok=False, secs=2.5)
            else:
                ok, err = connections.open_graph(p)
                flash(f"Opened {p}" if ok else f"Could not open graph: {err}",
                      ok=ok, secs=1.2 if ok else 2.5)
        elif ch == 'M':
            from .claude_md import memory_map_menu
            memory_map_menu(project_path, project_name)


def _toggle_hook(project_path, st, flash):
    from .config import load_settings, save_settings
    from . import hooks as hooks_mod
    s = load_settings()
    proj = s.setdefault('project_defaults', {}).setdefault(st['enc'], {})
    new_state = not st['hook_on']
    proj['memory_hook'] = new_state
    save_settings(s)
    if new_state and not hooks_mod.memory_hook_installed():
        hooks_mod.install_memory_hook()
    flash(f"Per-prompt hook {'ENABLED' if new_state else 'disabled'} for this project",
          ok=new_state, secs=1.6)
