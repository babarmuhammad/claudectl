"""A field the memory endpoints return must be a field the memory tabs render.

This is the generalisation of the whole rehaul. `/api/memory/state` returned
`est` — the digest cost, the recall budget and every rule file with its token
count — for its entire life, and `app.js` never once read it. Nothing caught
that: the route existed, the SPA called it, the payload was correct, and the
endpoint floor got its 200. The only observable symptom was a tab that could
not answer "what does memory cost me", which no gate can express.

So the gate is: enumerate the keys these four handlers put on the wire, and
require each one to appear in the renderer that consumes it. Scoped
deliberately to the memory surface — this is not a repo-wide rule, because a
payload key that nothing renders is a perfectly good outcome elsewhere (a
caller may want it for a computation, or for another client entirely).

Comments are prose, not behaviour: they are stripped first, or a key named only
in a "// TODO render est" would satisfy the check that est is rendered.
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'claude_sessions', 'web', 'app.js')

#: renderer -> the keys it is responsible for showing, per route. Each name is
#: a key some handler in gui_api.py puts on the wire; the value is the reason it
#: has to reach the screen, so a future edit that drops one has to argue with it.
SURFACE = {
    'drawMemory': [
        # /api/memory/state — the graph describing itself
        'n_entities', 'n_lessons', 'n_pending', 'n_unscanned', 'n_relations',
        'n_module_edges', 'n_modules', 'session_counter', 'generated_at',
        'hook_on', 'rules_on', 'auto_on', 'auto_updated', 'auto_last',
        # the cadence: a capped cycle leaves modules queued, and "until when"
        # has no answer without it
        'auto_interval',
        'pending_units', 'last_cost_usd', 'cost_usd_total', 'cost_history',
        'evicted_names', 'top', 'dirty', 'dirty_hook', 'budget', 'est',
        # a failed cycle and a capped one both left `pending_units` set, and
        # every consumer worded it "the next cycle takes them" — so six dead
        # calls an hour on a rate-limited account read as progress
        'last_failed', 'last_skipped', 'last_error',
        # reinforcement waiting to be folded in. The row showed the literal
        # string 'folding in', derived from the graph's top-hits list, which
        # is a different thing and never changed.
        'hits_pending',
        # "is this stale?" per artifact. The graph carries ONE build time while
        # the worklog, the rules and the two logs are each written by a
        # different code path, so no row could answer it.
        'written',
        # est.* — what memory costs a session, the reason this gate exists
        'digest_tokens', 'hook_budget',
        # /api/lessons — decay needs all three numbers, not just confidence
        'last_used', 'counter', 'ttl', 'kind',
        # /api/worklog
        'installed', 'session_id',
    ],
    # /api/workspace-status is fetched AFTER the paint (hashing files and
    # walking the transcript folders is the slowest thing the tab asks for), so
    # its fields land in the function that fills #wsBox. Same obligation, one
    # renderer further along.
    'fillWorkspace': ['checks', 'applicable', 'weight'],
    'drawClaudeMd': [
        'blocks', 'entries', 'present',     # /api/claude-md
        'imports',                          # /api/memory-map — broken @import
    ],
    'drawAudit': ['path'],                  # /api/ctxaudit — open the file
    'recallPrev': ['items'],                # why each entity was picked
}

_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT = re.compile(r'(?m)^[ \t]*//.*$')   # \s* would eat blank lines' newlines


def _source():
    """app.js with comments removed but its line numbering intact — a block
    comment collapses to the newlines it spanned, so a failure can name the
    line in the real file."""
    src = io.open(APP_JS, encoding='utf-8').read()
    return _LINE_COMMENT.sub('', _BLOCK_COMMENT.sub(
        lambda m: '\n' * m.group(0).count('\n'), src))


def _call_args(src, i):
    """Number of top-level arguments in a call whose '(' is at src[i - 1].

    JS silently discards an argument a function does not declare, so the only
    way to see the mistake is to count. Walks the call tracking bracket depth
    per code frame, both string kinds, and template literals — `${` opens a new
    frame, so a comma inside an interpolation is not an argument separator."""
    args, mode, depth, n = 1, ['code'], [0], len(src)
    while i < n:
        ch = src[i]
        m = mode[-1]
        if m == 'code':
            if ch in '([{':
                depth[-1] += 1
            elif ch in ')]}':
                if depth[-1] == 0:
                    if len(mode) == 1:          # the call's own ')'
                        return args
                    mode.pop(); depth.pop()     # end of a ${...}
                    i += 1
                    continue
                depth[-1] -= 1
            elif ch == ',' and depth[-1] == 0 and len(mode) == 1:
                args += 1
            elif ch in '\'"`':
                mode.append({'\'': 'sq', '"': 'dq', '`': 'tpl'}[ch])
        elif ch == '\\':
            i += 2
            continue
        elif m == 'sq' and ch == '\'' or m == 'dq' and ch == '"':
            mode.pop()
        elif m == 'tpl':
            if ch == '`':
                mode.pop()
            elif ch == '$' and src[i + 1:i + 2] == '{':
                mode.append('code'); depth.append(0)
                i += 2
                continue
        i += 1
    raise AssertionError('unterminated call')


#: helpers whose arguments are positional CELLS of a row. Passing one too many
#: does not raise — it shifts every cell left of it and drops the last.
_ROW_HELPERS = ('invRow', 'bucket', 'invTable', 'rch')


def test_no_row_helper_is_called_with_more_arguments_than_it_takes():
    """`invRow` lost its `reach` parameter when the flat 12-row table became
    four buckets, and all eleven call sites kept passing it. Nothing raised:
    the load-rule keyword rendered in the size column, the size rendered where
    the button goes, and the action was dropped. Every gate stayed green,
    because they all read a row's text and text does not know which cell it is
    in. Fewer arguments than declared is legal (omitted trailing ones), so this
    only rejects too many."""
    src = _source()
    bad = []
    for fn in _ROW_HELPERS:
        d = re.search(r'function %s\(([^)]*)\)' % re.escape(fn), src)
        assert d, f'{fn} not found in app.js'
        want = len([p for p in d.group(1).split(',') if p.strip()])
        for m in re.finditer(r'\b%s\(' % re.escape(fn), src):
            if src[m.start() - 8:m.start()].endswith('function'):
                continue
            got = _call_args(src, m.end())
            if got > want:
                line = src[:m.start()].count('\n') + 1
                bad.append(f'app.js:{line} calls {fn} with {got} args, it takes {want}')
    assert not bad, 'arguments silently dropped:\n  ' + '\n  '.join(bad)


def _slice(src, fn):
    """The body of `fn`, from its declaration to the next top-level one."""
    m = re.search(r'(?m)^(?:async )?function %s\(' % re.escape(fn), src)
    assert m, f'{fn} not found in app.js'
    nxt = re.search(r'(?m)^(?:async )?function \w+\(', src[m.end():])
    return src[m.start():m.end() + (nxt.start() if nxt else len(src))]


def test_every_memory_field_on_the_wire_reaches_the_screen():
    src = _source()
    missing = []
    for fn, keys in SURFACE.items():
        body = _slice(src, fn)
        for k in keys:
            if not re.search(r'\b%s\b' % re.escape(k), body):
                missing.append(f'{fn} never reads {k!r}')
    assert not missing, (
        'these fields are computed, serialised and thrown away:\n  '
        + '\n  '.join(missing))


def test_the_memory_tab_does_not_wait_on_the_slow_call_before_painting():
    """`/api/workspace-status` hashes the repo's key files and walks every
    transcript folder of every account — 4.6s on this repo before the handler
    was cut down, and still by far the most expensive thing the tab asks for.
    Awaiting it inside `drawMemory` made the ENTIRE page wait on a card that is
    below the fold, so the tab looked broken while three fast calls sat
    finished. It belongs in the fill that runs after `paint()`."""
    src = _source()
    body = _slice(src, 'drawMemory')
    assert 'workspace-status' not in body, \
        'drawMemory awaits the slow call again — the page waits on it'
    fill = _slice(src, 'fillWorkspace')
    assert 'workspace-status' in fill
    # and it must survive a navigation away mid-fetch: the node it writes has
    # to be re-queried after the await, never captured before it
    assert re.search(r"const b=\$\('#wsBox'\);if\(!b\)return;", fill), \
        'fillWorkspace writes a node it did not re-query after the await'


def test_the_gate_covers_the_keys_the_handlers_actually_send():
    """The list above is hand-written, so it can fall behind the handlers it
    describes. Pin it to the source: every `'key':` literal in the four memory
    handlers is either listed above or named here as deliberately not shown."""
    from claude_sessions import gui_api
    import inspect

    #: keys a renderer has no business showing. Each needs a reason.
    NOT_SHOWN = {
        'lessons', 'text', 'exists', 'tokens', 'files', 'label', 'ref',
        'name', 'summary', 'status', 'confidence', 'id', 'key',   # row fields
        'on', 'entries', 'score', 'safe', 'state', 'detail',      # rendered by value
        'evicted',          # the count; `evicted_names` is what is shown
        'last_extracted',   # superseded by auto_last.extracted
        'context', 'empty', 'items', 'path', 'blocks', 'present', 'imports',
        'generated_at',     # workspace-status copy; the memory one is shown
        'module', 'hits',   # inside `top`, rendered from the object
    }
    listed = {k for ks in SURFACE.values() for k in ks} | NOT_SHOWN
    unlisted = set()
    for h in (gui_api.api_memory_state, gui_api.api_lessons_get,
              gui_api.api_workspace_status, gui_api.api_claude_md_get):
        for k in re.findall(r"'([a-z_]+)':", inspect.getsource(h)):
            if k not in listed:
                unlisted.add(f'{h.__name__} sends {k!r}')
    assert not unlisted, (
        'new payload fields that neither render nor say why not:\n  '
        + '\n  '.join(sorted(unlisted)))


def test_no_stub_is_silently_overridden_by_a_later_one():
    """A duplicate key in `tools/smoke_gui.py`'s ROUTES dict is not an error in
    Python — the later entry simply wins, and the earlier one becomes dead text
    that reads exactly like coverage. Two were live when this was written: a
    stale `/api/history` shape that would have masked the graph work, and an
    empty `/api/skills` shadowed by the real one. A dict literal cannot check
    itself, so walk the source."""
    import ast
    from collections import Counter
    src = io.open(os.path.join(ROOT, 'tools', 'smoke_gui.py'), encoding='utf-8').read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'ROUTES':
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            dupes = sorted(k for k, n in Counter(keys).items() if n > 1)
            assert not dupes, f'later stubs silently win for: {dupes}'
            return
    raise AssertionError('ROUTES not found in tools/smoke_gui.py')
