"""A GUI job may not reach a keyboard.

`gui_api.start_job` runs its handler on a thread and `_install_bridge` patches
exactly five interactive primitives — `ui.flash`, `ui.text_input`, `ui.confirm`,
`diffview.confirm`, `claude_md._pager_confirm` — plus the by-value copies those
modules made of them. Anything else that reads a key BLOCKS: `ui.wait_event` is
a `time.sleep(0.03)` spin that only returns on a real keypress, and there is no
keypress coming. So the job stays 'running' until the six-hour stuck-reaper
(`gui_api._STUCK_AFTER`), which is what "AI generate agents doesn't work" was.

`tests/test_quota.py` already stated the invariant in a comment — *"`ui.menu` is
not bridged for job threads — reaching it there is a hang, not an error"* — and
three job kinds violated it anyway: `agent_ai` (`menu` via `_pick_category`),
`hook_ai` (`confirm`) and `ai_scaffold` (`wait_event`). A comment is not a gate.

The check is deliberately shallow-but-total: the entry point of every job kind,
one hop into the functions it names. A full call-graph walk would need to
resolve attributes across modules, and the shape that actually bit is a handler
calling a blocking primitive one or two frames down.
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'claude_sessions')

#: primitives that block on a keypress. `flash`/`text_input`/`confirm` and the
#: two confirm-with-content ones are bridged, so they are allowed; these are not.
BLOCKING = {'menu', 'wait_event', 'poll_event', 'pause', 'pager', 'multiselect',
            'push_event', 'flush_input'}

#: bridged — the whole point of the bridge is that these are safe on a job thread
BRIDGED = {'flash', 'text_input', 'confirm', '_pager_confirm'}


def _tree(mod):
    path = os.path.join(PKG, mod + '.py')
    return ast.parse(open(path, encoding='utf-8').read(), filename=path), path


def _job_kind_handlers():
    """(kind, module, funcname) for every `elif kind == '…'` branch in
    `api_job_start`, read off the source so this cannot fall behind the dispatch
    chain — the same reason `tests/test_endpoint_floor.py` parametrizes off the
    live route tables."""
    tree, _ = _tree('gui_api')
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'api_job_start')
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == 'kind'):
            continue
        for c in node.comparators:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                out.append(c.value)
    assert len(out) > 20, f'only found {len(out)} job kinds — parse broke'
    return sorted(set(out))


#: the predicate that makes a key read legal: it is only reached in the terminal.
#: A flow too tangled to split (the streamed CLAUDE.md analyzer) may branch on
#: this instead, and then the blocking call is genuinely unreachable from a job.
GUARD = '_on_job_thread'


def _calls_in(fn, guarded_ok=True):
    """Bare names called inside a function, plus `x.attr()` attribute names.

    With `guarded_ok`, calls that sit lexically inside a branch testing
    `_on_job_thread()` are dropped: they cannot run on a job thread, which is
    the whole claim being checked.
    """
    skip = set()
    if guarded_ok:
        for node in ast.walk(fn):
            if not isinstance(node, (ast.If, ast.While)):
                continue
            if GUARD not in ast.dump(node.test):
                continue
            for branch in (node.body, node.orelse):
                for st in branch:
                    skip.update(id(n) for n in ast.walk(st))
    names = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or id(node) in skip:
            continue
        f = node.func
        if isinstance(f, ast.Name):
            names.add(f.id)
        elif isinstance(f, ast.Attribute):
            names.add(f.attr)
    return names


def _funcs(mod):
    tree, _ = _tree(mod)
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


#: modules a job handler runs in. Not every module in the package: this is the
#: set the dispatch chain actually calls into, which is what keeps the check
#: from becoming "no module may ever open a menu".
JOB_MODULES = ('agents', 'hooks', 'skills', 'claude_md', 'memory', 'lessons',
               'brief', 'review', 'mcp', 'memrules', 'plan_execute',
               'provision', 'versions', 'omniroute', 'failover', 'gui_api')


@pytest.mark.parametrize('mod', JOB_MODULES)
def test_no_job_entry_point_can_block_on_a_keypress(mod):
    """The functions a job kind names, and one hop past them.

    A hit here is a HANG in production, not an exception — nothing logs, nothing
    times out short of six hours, and the UI says "running" the whole time.
    """
    funcs = _funcs(mod)
    # the entry points: anything a job kind's branch could name in this module,
    # approximated as every module-level function the dispatch chain mentions
    gui_tree, _ = _tree('gui_api')
    start = next(n for n in ast.walk(gui_tree)
                 if isinstance(n, ast.FunctionDef) and n.name == 'api_job_start')
    mentioned = _calls_in(start) | {
        n.id for n in ast.walk(start) if isinstance(n, ast.Name)}

    offenders = []
    # a bridged primitive's own BODY reads keys — that is its TUI
    # implementation, and the bridge replaces the whole function on a job
    # thread, so walking into it would flag the very thing that makes this safe
    seen = set(BRIDGED)
    frontier = [name for name in funcs if name in mentioned and name not in seen]
    for _hop in range(2):                    # entry point, then one level in
        nxt = []
        for name in frontier:
            if name in seen:
                continue
            seen.add(name)
            called = _calls_in(funcs[name])
            for bad in sorted(called & BLOCKING):
                offenders.append(f'{mod}.{name} calls {bad}()')
            nxt += [c for c in called if c in funcs and c not in seen]
        frontier = nxt

    assert not offenders, (
        'a GUI job can reach a blocking keyboard primitive — this HANGS the job '
        'until the 6-hour reaper:\n  ' + '\n  '.join(offenders)
        + '\nSplit the flow into a non-interactive function the job calls and a '
          'TUI wrapper that keeps the menus (see agents.generate_agent_ai / '
          'skills.build_ai_prompt), or route the approval through the already-'
          'bridged claude_md._pager_confirm.')


def test_the_three_ai_generators_have_a_non_interactive_entry_point():
    """The positive half: each of the three that hung now has a function the job
    can call which does not read a key. Named explicitly, because the walk above
    can only prove the absence of the old shape, not the presence of the fix."""
    from claude_sessions import agents, hooks, skills
    assert callable(agents.generate_agent_ai)
    assert callable(agents.build_ai_prompt) and callable(agents.write_agent_raw)
    assert callable(skills.build_ai_prompt) and callable(skills.write_skill_raw)
    # hooks._ai_hook is reachable because its only two unbridged calls became
    # bridged ones — the gate is `_pager_confirm`, the messages are `flash`
    src = open(hooks.__file__, encoding='utf-8').read()
    assert '_pager_confirm(' in src, 'the hook gate is not the bridged one'


def test_every_by_value_copy_of_a_bridged_primitive_is_patched_too():
    """`from .ui import text_input` binds a SECOND name, and patching `ui` does
    not move it.

    This is the "import-time binding" bug this codebase has now paid for four
    times. `_install_bridge` patched `ui.text_input` and `hooks.text_input` and
    not `claude_md.text_input` — and `claude_md` is the module whose
    `_on_job_thread()` branch calls it, so the job kind the whole split was
    written to fix (`ai_scaffold`) still hung. The walk above cannot see it:
    `text_input` is BRIDGED, so it is allowed by name.
    """
    import importlib

    from claude_sessions import gui_api, ui                  # noqa: F401
    assert gui_api                                           # bridge installed

    missing = []
    for name in ('flash', 'text_input', 'confirm'):
        for mod in JOB_MODULES:
            m = importlib.import_module('claude_sessions.' + mod)
            if hasattr(m, name) and getattr(m, name) is not getattr(ui, name):
                missing.append(f'{mod}.{name}')
    assert not missing, (
        'these modules hold their own reference to a bridged primitive, so a '
        'GUI job calling one HANGS:\n  ' + '\n  '.join(sorted(missing))
        + '\ngui_api._install_bridge sweeps sys.modules for the original '
          'function object — a module missed here was imported after the sweep '
          'AND rebound the name, or the sweep broke.')


def test_every_job_kind_is_dispatched():
    """Guards the parse the walk above depends on: if `api_job_start` stops being
    an if/elif chain on `kind`, `_job_kind_handlers` silently finds nothing and
    this whole file passes by doing nothing — the `smoke_gui.py` lesson."""
    kinds = _job_kind_handlers()
    for expected in ('agent_ai', 'hook_ai', 'ai_scaffold', 'skill_ai',
                     'memory_build', 'plan_make'):
        assert expected in kinds, expected
