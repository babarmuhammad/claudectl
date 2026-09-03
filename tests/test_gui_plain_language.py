"""Claude Code's vocabulary must not be the only thing on screen.

The Agents, Skills, Hooks and Claude Code pages listed `PreToolUse`,
`effortLevel`, `01-core-development` and `user` and left the reader to already
know what those mean — which is fine for someone who has read the docs and
useless for the person the GUI exists for. The fix in each case is the same
shape: a plain-English phrase leads, the API name follows, and the phrase lives
in ONE place next to the thing it names rather than being retyped per surface.

Three things can silently undo that, and each has a test here:

  1. a new Claude Code event, or a new settings group, arriving with no phrase
     — the screen falls back to the raw name and nobody notices, because
     falling back is exactly what it is designed to do
  2. the phrase never reaching the wire, or reaching it and not being read —
     the same class `test_gui_memory_surface.py` was written for
  3. the grouping breaking its two structural contracts: a filtered-away group
     leaving its heading standing over nothing, and the group wrapper inside
     the settings GRID becoming a cell of its own

Comments are stripped from app.js before any of it is searched: prose naming a
field is not the same as a renderer showing it.
"""

import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import Sandbox
from claude_sessions import (agents, ccsettings, claude_md, ctxaudit, gui_api,
                             hooks, memhub)
from claude_sessions import config as _cfg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'claude_sessions', 'web', 'app.js')
APP_CSS = os.path.join(ROOT, 'claude_sessions', 'web', 'app.css')

_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT = re.compile(r'(?m)^[ \t]*//.*$')


def _js():
    """app.js with its comments removed — see test_gui_memory_surface."""
    src = io.open(APP_JS, encoding='utf-8').read()
    return _LINE_COMMENT.sub('', _BLOCK_COMMENT.sub('', src))


# ── 1. every name the screen shows has a phrase ──────────────────────────────

def test_every_hook_event_says_when_it_fires():
    """A hooks screen that lists nothing but event names can only be read by
    someone who has already memorised them."""
    missing = sorted(set(hooks.EVENT_MATCHERS) - set(hooks.EVENT_WHEN))
    assert not missing, 'no plain-English phrase for: %s' % ', '.join(missing)
    for ev, when in hooks.EVENT_WHEN.items():
        # a phrase that is the event name again is the fallback wearing a
        # disguise, and it would satisfy a bare membership check
        assert when and when != ev, ev
        assert ' ' in when, '%s: %r is a label, not a phrase' % (ev, when)


def test_every_settings_group_says_what_it_is_for():
    """"Teams & extensions" tells someone who already knows Claude Code where
    to look, and tells everybody else nothing."""
    missing = [g for g in ccsettings.GROUPS if not ccsettings.GROUP_HELP.get(g)]
    assert not missing, 'no group help for: %s' % ', '.join(missing)


def test_group_help_names_no_group_that_is_gone():
    """The other direction: help for a group no key is in is dead prose that
    reads as coverage."""
    stale = sorted(set(ccsettings.GROUP_HELP) - set(ccsettings.GROUPS))
    assert not stale, 'help for groups that no longer exist: %s' % ', '.join(stale)


# ── 2. the phrase reaches the wire, and the wire reaches the screen ──────────

def test_the_hooks_payload_carries_the_phrases_and_the_screen_reads_them():
    d = gui_api.api_hooks_get({}, {})
    assert d['events'] == hooks.EVENT_WHEN
    for t in d['templates']:
        assert t['event'] in hooks.EVENTS, t['key']
    src = _js()
    assert 'HKWHEN=d.events' in src, 'the phrases are on the wire and unread'
    assert 'hkWhen(' in src


def test_the_settings_payload_carries_the_group_help_and_the_screen_reads_it():
    d = gui_api.api_cc_settings_get({}, {})
    assert d['group_help'] == ccsettings.GROUP_HELP
    src = _js()
    assert 'group_help' in src, 'the group help is on the wire and unread'


# ── 3. the two structural contracts of grouping ──────────────────────────────

def test_a_filtered_group_takes_its_heading_with_it():
    """`bindFilter` hides ROWS. A group header is not a row, so without this
    sweep a search leaves every heading standing over nothing."""
    src = _js()
    assert 'class="fgrp"' in src, 'nothing groups rows any more — drop this test'
    sweep = re.search(r"querySelectorAll\('#content details[^']*'\)", src)
    assert sweep, 'bindFilter no longer sweeps group containers'
    assert '.fgrp' in sweep.group(0), \
        'bindFilter hides empty <details> but not empty .fgrp groups'


def test_the_settings_grid_does_not_collapse_under_its_group_wrapper():
    """`.cctable` is a CSS grid and `.ccrow` is `display:contents`. A wrapper
    that is not also `display:contents` becomes one grid CELL, and every row
    inside it collapses into a single column."""
    css = io.open(APP_CSS, encoding='utf-8').read()
    assert re.search(r'\.cctable\s+\.fgrp\s*\{[^}]*display:\s*contents', css), \
        '.cctable .fgrp must be display:contents or the grid collapses'


# ── 4. the raw name is demoted, never deleted ────────────────────────────────

def test_the_raw_names_still_show():
    """The phrase leads; the identifier still has to be there, because it is
    what the documentation, settings.json and a web search all use."""
    src = _js()
    # hooks: the event name rides beside its phrase
    assert 'hkWhen(e)' in src and 'esc(e)' in src
    # settings: the camelCase key stays under the readable label
    assert 'esc(ccName(k))' in src and '<code>${esc(k)}</code>' in src
    # agents: the category is prettified for display and kept for the filter
    assert 'agCat(' in src


def test_a_category_folder_name_is_read_as_words():
    """`01-core-development` is a sort key wearing a heading's clothes."""
    src = _js()
    body = re.search(r'function agCat\(c\)\{(.*?)\n\}', src, re.S)
    assert body, 'agCat is gone — the category headings are raw folder names again'
    # the ordering prefix must be stripped, not just the separators
    assert r'^\d+[-_]' in body.group(1)


# ── 5. an agent is filed, and its filing survives ────────────────────────────

@pytest.fixture
def acct(monkeypatch, tmp_path):
    """The sandbox's one account, so creating an agent writes where
    `all_installed` will later look for it — the same directory, or the
    round-trip proves nothing."""
    sb = Sandbox(monkeypatch, tmp_path)
    return sb.cfg


def test_the_category_you_pick_survives_the_round_trip(acct):
    """The category cannot live in a subfolder — Claude Code reads the ONE
    `agents/` directory, so an agent filed into `agents/reviewers/` would stop
    being loaded at all. It rides in the frontmatter, and that has to come back
    out again."""
    r = gui_api.api_agent_create({}, {'name': 'diff-reader', 'description': 'reads diffs',
                                      'category': 'Quality security', 'scope': 'user',
                                      'path': '', 'body': 'do the thing'})
    assert r['ok']
    assert agents.category_of(r['file']) == 'Quality security'
    meta, _body = agents.parse_agent(r['file'])
    assert meta['name'] == 'diff-reader', 'the known keys must survive too'
    assert 'Quality security' in agents.installed_categories()


def test_an_unfiled_agent_gets_a_heading_not_a_gap(acct):
    r = gui_api.api_agent_create({}, {'name': 'scratch', 'description': 'odd jobs',
                                      'category': '   ', 'scope': 'user',
                                      'path': '', 'body': 'x'})
    assert agents.category_of(r['file']) == agents.NO_CATEGORY
    # …and blank is not offered back as a category you could pick
    assert agents.NO_CATEGORY not in agents.installed_categories()


def test_the_form_offers_the_categories_that_already_exist(acct):
    gui_api.api_agent_create({}, {'name': 'a', 'description': 'd',
                                  'category': 'Invented', 'scope': 'user',
                                  'path': '', 'body': 'x'})
    d = gui_api.api_agents_library({}, {})
    assert 'Invented' in d['category_names'], \
        'a category you invented must be offered for the next agent'
    src = _js()
    assert 'list:d.category_names' in src, 'the picker has nothing to offer'
    # one control that both picks and accepts a new name — a <select> alone
    # cannot express "or something you have not thought of yet". Matched as the
    # whole conditional, because `'<datalist' in src` also passes for a typo
    # that renders nothing.
    assert re.search(r'f\.list\?`<datalist id="pfl\$\{i\}">', src), \
        'ask() no longer renders a datalist for a `list:` field'


def test_your_own_agents_are_divided_by_category():
    src = _js()
    # the CALL, not the definition — a helper nobody calls groups nothing
    assert 'const own=agByCat(' in src, 'the installed agents are a flat list again'
    body = re.search(r'function agByCat\(rows,render\)\{(.*?)\n\}', src, re.S)
    assert body and 'fgrp' in body.group(1), \
        'a category group must be filterable like every other group'
    assert 'NO_CAT?1:' in body.group(1), 'the unfiled heading must sort LAST'


# ── 6. CLAUDE.md tells the truth about who wrote each block ──────────────────

#: every block claudectl writes into a PROJECT CLAUDE.md, and the sentinel pair
#: that fences it. `split_blocks` has to know all of them, because whatever it
#: does not know is reported as YOUR prose.
_PROJECT_BLOCKS = {
    'autogen': (_cfg._AUTOGEN_START, _cfg._AUTOGEN_END),
    'sessions': (_cfg._SESSIONS_START, _cfg._SESSIONS_END),
    'memory': (_cfg._MEMORY_START, _cfg._MEMORY_END),
    'agents': (_cfg._AGENTS_START, _cfg._AGENTS_END),
    'loop': (_cfg._LOOP_START, _cfg._LOOP_END),
}


def _md_with_every_block():
    parts = ['# proj', '', 'MY OWN PROSE.', '']
    for key, (start, end) in _PROJECT_BLOCKS.items():
        parts += [start, 'generated %s content' % key, end, '']
    return '\n'.join(parts)


def test_no_generated_block_is_counted_as_your_prose():
    """`split_blocks` knew three of the five. The subagent table and the loop
    log therefore landed in `manual`, which is the bucket the CLAUDE.md tab
    labels "you write it" and the compressor is handed to reword."""
    b = ctxaudit.split_blocks(_md_with_every_block())
    for key in _PROJECT_BLOCKS:
        assert b[key], 'split_blocks lost the %s block' % key
        assert 'generated %s content' % key not in b['manual'],             '%s is being reported as your own prose' % key
    assert 'MY OWN PROSE.' in b['manual'], 'the real manual text must survive'


def test_a_rewrite_carries_every_generated_block_across():
    """The other half of the same change: taking a block out of `manual` means
    the AI rewrite never sees it, so something has to put it back. Only MEMORY
    was carried, so splitting the other two out without this would have DELETED
    them on the first compress."""
    existing = _md_with_every_block()
    rewritten = '# proj\n\nA MUCH SHORTER FILE.\n'
    out = claude_md._preserve_machine_blocks(rewritten, existing)
    for key, (start, _end) in _PROJECT_BLOCKS.items():
        if key in ('autogen', 'sessions'):
            continue          # rebuilt from scratch on the same pass, by design
        assert start in out, '%s was dropped by the rewrite' % key
        assert out.count(start) == 1, '%s ended up in the file twice' % key
    assert 'A MUCH SHORTER FILE.' in out


def test_every_generated_block_says_who_wrote_it_and_how_to_rebuild():
    """A bare sentinel names no tool, states no prohibition and offers no way
    back, so a hand edit inside it looks safe and is silently replaced. The
    rule is structural: a module that OPENS a machine block must also write the
    notice."""
    root = os.path.join(ROOT, 'claude_sessions')
    openers = [n for n in dir(_cfg)
               if n.endswith('_START') and isinstance(getattr(_cfg, n), str)]
    seen = 0
    for name in sorted(os.listdir(root)):
        if not name.endswith('.py'):
            continue
        src = io.open(os.path.join(root, name), encoding='utf-8').read()
        # interpolated into a block being BUILT, not merely searched for
        if not any(('{%s}' % o) in src for o in openers):
            continue
        seen += 1
        assert 'generated_note' in src,             '%s writes a machine block with no "do not edit / rebuild" notice' % name
    assert seen >= 3, 'expected several block writers, found %d' % seen


def test_the_notice_carries_the_three_things_that_make_it_useful():
    note = _cfg.generated_note('the graph', 'the Memory tab')
    assert note.startswith('<!--') and note.endswith('-->'),         'it must be an HTML comment — Anthropic strips those before injection'
    assert 'claudectl' in note, 'name the tool'
    assert 'Do not edit' in note, 'state the prohibition'
    assert 'the Memory tab' in note, 'say how to get it back'


def test_the_notice_is_not_in_the_sentinel_itself():
    """Twenty readers match the opener as a literal string. Moving the text
    INTO the marker would stop matching an existing file and append a second
    copy of every block — the migration this deliberately avoids."""
    assert _cfg._AUTOGEN_START == '<!-- AUTOGEN:START -->'
    assert _cfg._MEMORY_START == '<!-- CLAUDECTL:MEMORY:START -->'


def test_the_tab_says_who_writes_each_block(acct, tmp_path):
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'CLAUDE.md').write_text(_md_with_every_block(), encoding='utf-8')
    d = gui_api.api_claude_md_get({'path': str(proj)}, {})
    keys = [b['key'] for b in d['blocks']]
    for key in _PROJECT_BLOCKS:
        assert key in keys, 'the tab cannot name the %s block' % key
    src = _js()
    # scoped to CMWHAT, not the whole file: CMFIX carries the very same keys,
    # so a bare search finds the BUTTON map and passes with no description
    what = re.search(r'const CMWHAT=\{(.*?)\n\};', src, re.S)
    assert what, 'CMWHAT is gone — the rows say who wrote it but never what it is'
    for key in list(_PROJECT_BLOCKS) + ['manual', 'keep']:
        assert re.search(r'(?m)^\s*%s:\[' % key, what.group(1)), \
            'CMWHAT has no line for the %s block' % key
    assert 'claudectl writes it' in src and 'you write it' in src


# ── 7. "is this stale?", per artifact ────────────────────────────────────────

def test_only_an_artifact_that_is_one_file_gets_dated(acct, tmp_path):
    """A stamp inside the data is a second thing to keep in step with the
    write; an mtime cannot disagree with what is on disk. But a CLAUDE.md block
    shares a file with your prose, so dating it off that file would call the
    digest fresh because you fixed a typo — those rows stay bare."""
    proj = tmp_path / 'proj'
    (proj / '.claudectl' / 'memory').mkdir(parents=True)
    (proj / 'CLAUDE.md').write_text('# proj\n', encoding='utf-8')
    assert memhub.last_written(str(proj), None) == {},         'nothing written yet, so nothing may claim a date'
    (proj / '.claudectl' / 'memory' / 'worklog.json').write_text('[]', encoding='utf-8')
    w = memhub.last_written(str(proj), None)
    assert set(w) == {'worklog'}, w
    assert w['worklog'] > 0
    # the CLAUDE.md-embedded blocks are never dated from the file they sit in
    assert 'digest' not in w and 'autogen' not in w and 'sessions' not in w


def test_the_tab_shows_the_age_and_the_wire_carries_it():
    src = _js()
    assert 'st.written' in src, 'the freshness is on the wire and unread'
    assert re.search(r'function invRow\(name,does,where,size,action,when\)', src),         'invRow no longer takes the artifact age'
    assert 'updated ${ago(when)}' in src
