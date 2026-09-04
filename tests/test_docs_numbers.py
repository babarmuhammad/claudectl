"""Published counts must equal the ones the code actually holds.

Every number here had drifted at least once by the time this file was written:
the README and the marketing site advertised 29 palettes / 7 skins against a
`themes.py` holding 32 and 8, and 19 hook templates against 31. Nothing failed,
because nothing was looking — the docs are prose, and prose does not import.

Scope is deliberately narrow: only counts whose source of truth is *in this
repository*, so the assertion can be exact. The test count is not here (it would
fail every pull request that adds a test — `tools/gen_metrics.py` writes that
badge instead), and neither is the size of the upstream subagent catalog, which
lives in somebody else's repository and is written as "150+" for that reason.

Text reads only, like `tests/test_docs_site.py` — the `test` job installs pytest
and nothing else.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from claude_sessions import hooks, themes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Every surface that states a count in prose. `docs/api.md` and
#: `docs/dashboard.md` are generated and guarded elsewhere, so they are not here.
SURFACES = (['README.md', 'CLAUDE.md', 'docs/llms.txt',
             'www/lib/content.ts']
            + sorted(os.path.relpath(p, ROOT).replace(os.sep, '/')
                     for p in glob.glob(os.path.join(ROOT, 'docs', '*.md'))
                     if os.path.basename(p) not in ('api.md', 'dashboard.md')))

WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6,
         'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'nineteen': 19,
         'twenty': 20, 'thirty-one': 31, 'thirty-two': 32}


def _count():
    """What the code says, right now."""
    return {
        # A world's palette is `hidden` — it is not one of the ones you pick
        # from, and the docs are describing the picker.
        'palettes': sum(1 for p in themes.PALETTES.values() if not p.get('hidden')),
        'skins': len(themes.SKINS),
        'worlds': len(themes.WORLDS),
        'hook templates': len(hooks.TEMPLATES),
        'plugin commands': len(glob.glob(os.path.join(ROOT, 'plugin', 'commands', '*.md'))),
        'skill templates': len(glob.glob(os.path.join(
            ROOT, 'claude_sessions', 'skills_templates', '*', 'SKILL.md'))),
    }


#: (label, regex). The regex captures the number as group 1 and must match the
#: unit too, or `8 skins` and `8 accounts` become the same claim.
PATTERNS = (
    ('palettes', r'([\w-]+) palettes'),
    ('skins', r'([\w-]+) skins'),
    ('worlds', r'([\w-]+) (?:themed )?worlds'),
    ('hook templates', r'([\w-]+) (?:ready-made |Claude Code )?hook templates'),
    ('hook templates', r'([\w-]+) ready-made (?:Claude Code )?hook templates'),
    ('hook templates', r'\*\*([\w-]+) ready-made templates\*\*'),
    ('plugin commands', r'([\w-]+) slash commands'),
    ('skill templates', r'([\w-]+) skills the plugin adds'),
    ('skill templates', r'slash commands and ([\w-]+) skills'),
)


def _read(rel):
    with open(os.path.join(ROOT, rel.replace('/', os.sep)), encoding='utf-8') as f:
        return f.read()


def _num(token):
    """`32`, `1,470` or `thirty-one` — or None when it is not a number at all
    (`the palettes`, `world palettes`), which is a sentence this rule has no
    business policing."""
    token = token.strip().lower()
    if token in WORDS:
        return WORDS[token]
    token = token.replace(',', '')
    return int(token) if token.isdigit() else None


@pytest.mark.parametrize('rel', SURFACES)
def test_every_count_a_page_publishes_matches_the_code(rel):
    want = _count()
    text = _read(rel)
    wrong = []
    for label, pattern in PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            got = _num(m.group(1))
            if got is not None and got != want[label]:
                wrong.append('%r says %s %s, code says %d'
                             % (m.group(0).strip(), m.group(1), label, want[label]))
    assert not wrong, '%s is out of date:\n  %s' % (rel, '\n  '.join(wrong))


def test_the_patterns_actually_match_something():
    """Every rule above is one regex away from silently matching nothing — which
    is the failure mode this whole file exists to catch, one level up. So each
    label has to be found somewhere, or the rule is decoration."""
    text = '\n'.join(_read(rel) for rel in SURFACES)
    for label in sorted({label for label, _ in PATTERNS}):
        hits = [m for _l, p in PATTERNS if _l == label
                for m in re.finditer(p, text, re.I) if _num(m.group(1)) is not None]
        assert hits, 'no page states a %s count — the pattern has gone stale' % label
