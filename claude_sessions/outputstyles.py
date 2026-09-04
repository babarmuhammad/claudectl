"""Output styles — the last unmanaged Claude Code config surface.

An output style replaces the system prompt's "how to behave" section: same
tools, same permissions, different job. Claude Code ships `default`,
`Explanatory` and `Learning`; custom ones are markdown files with YAML
frontmatter in `~/.claude/output-styles/` (user) or `.claude/output-styles/`
(project), selected by `outputStyle` in the corresponding settings.json.

It sits beside skills, agents and hooks in claudectl for one reason worth
stating: it is *per project and per account*, and claudectl is the only thing
here that already knows which project and which account you are launching. A
style that suits a code review is wrong for a refactor, and switching it by
hand in a JSON file is exactly the friction this tool exists to remove.
"""

import os
import re

from . import config as _c

#: shipped with Claude Code; present in the picker, not on disk
BUILTIN = [
    ('default', 'Claude Code as it ships — efficient software engineering.'),
    ('Explanatory', 'Explains its reasoning and the codebase as it works.'),
    ('Learning', 'Collaborative: asks you to write pieces of the code.'),
]

#: What a built-in has instead of a file. Viewing one used to show "(empty)",
#: which reads as a broken button rather than as "there is nothing to show":
#: Anthropic ships these inside Claude Code and their text is not on disk.
BUILTIN_NOTE = (
    "This style ships inside Claude Code, so there is no file on this machine "
    "to show or edit.\n\n%s\n\nTo change how it behaves, copy a starter below "
    "or write your own style — a custom style with the same job replaces this "
    "one for the scope you save it in."
)

#: claudectl's own starters. Inline rather than package data on purpose: a
#: `package-data` glob that matches nothing fails SILENTLY (it shipped an empty
#: skills_templates/ for a whole release), and four short documents are not
#: worth that risk. Each one is a real job, none of them overlaps the three
#: Claude Code already ships.
STARTERS = [
    {'name': 'Terse',
     'description': 'Answers only. No preamble, no recap, no restating the code.',
     'body': """Answer directly. Nothing before the answer, nothing after it.

- No preamble ("I'll help you with that"), no recap of what was just done, no
  closing summary when the result is visible from the change itself.
- Never re-print unchanged code. Reference it as `file.py:42` instead.
- Explain only what was asked, at the depth it was asked at. A one-line
  question gets a one-line answer.
- Keep every technical detail: shorter, not vaguer. Exact names, exact errors,
  exact numbers. If a caveat changes what the user should do, it stays.
- Code, commit messages and anything written to a file are unaffected — this
  governs the conversation, not the artifacts."""},
    {'name': 'Reviewer',
     'description': 'Findings first, severity-tagged, no praise, no scope creep.',
     'body': """Review the code. Do not rewrite it unless asked.

- Lead with the findings, worst first. One line each:
  `path:line — severity: what breaks. The fix.`
- Severity means consequence: **critical** (data loss, auth bypass, corruption),
  **high** (wrong result, crash on a real input), **medium** (fragile, will
  break on the next change), **low** (clarity).
- Every finding names a concrete failure: the input, the state, the wrong
  output. A finding you cannot make fail is a preference — say so or drop it.
- No praise, no "consider maybe", no style nits unless they change meaning.
- Say plainly when there is nothing to report. An empty review is a result."""},
    {'name': 'Pair',
     'description': 'States the plan before touching anything, then works one step at a time.',
     'body': """Work like a pair-programmer with the keyboard.

- Before editing: one short paragraph on what you are about to change and why,
  naming the files. Then do it.
- One step at a time. Finish and report a step before starting the next.
- Stop and ask before anything wide or hard to undo: a rename across files, a
  schema change, deleting something you did not write, a new dependency.
- After each step say what you verified — the command you ran and its result,
  not a claim that it should work.
- Disagree when the request looks wrong: say why in a sentence, then do it
  their way if they confirm."""},
    {'name': 'Ship',
     'description': 'Change plus one line of what and how to check it. Nothing else.',
     'body': """Deliver the change, not an essay about it.

- Make the edit. Then: one line saying what changed, and the exact command to
  verify it.
- No explanation of code the user can read, no restating the request, no
  options you did not take.
- If something is genuinely ambiguous, pick the option a careful colleague
  would, state the assumption in one line, and continue. Do not stall.
- Report failures with the actual output. Never claim a test passed without
  having run it."""},
]


def starters():
    """The starters, as list rows the UI can render beside real styles."""
    return [dict(s, scope='starter', builtin=False, file='',
                 lines=s['body'].count('\n') + 1) for s in STARTERS]

_FM = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.S)


def _dirs(project_path=None, cfgdir=None):
    """[(scope, dir)] — user first, project second (project wins in the UI)."""
    out = [('user', os.path.join(cfgdir or _c.config_dir, 'output-styles'))]
    if project_path:
        out.append(('project',
                    os.path.join(project_path, '.claude', 'output-styles')))
    return out


def _parse(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError:
        return None
    m = _FM.match(text)
    meta, body = {}, text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            k, _, v = line.partition(':')
            if _:
                meta[k.strip()] = v.strip().strip('"\'')
    name = meta.get('name') or os.path.splitext(os.path.basename(path))[0]
    return {'name': name,
            'description': meta.get('description', ''),
            'file': path,
            'body': body.strip(),
            'lines': body.count('\n') + 1}


def listing(project_path=None, cfgdir=None):
    """Built-ins plus every custom style, with the active one marked."""
    active = current(project_path, cfgdir)
    styles = [{'name': n, 'description': d, 'file': '', 'builtin': True,
               'scope': 'built-in', 'lines': 0} for n, d in BUILTIN]
    for scope, d in _dirs(project_path, cfgdir):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith('.md'):
                continue
            s = _parse(os.path.join(d, fn))
            if s:
                s.pop('body', None)
                s.update(builtin=False, scope=scope)
                styles.append(s)
    for s in styles:
        s['active'] = (s['name'] == active)
    return styles


def _settings_path(project_path=None, cfgdir=None):
    if project_path:
        return os.path.join(project_path, '.claude', 'settings.json')
    return os.path.join(cfgdir or _c.config_dir, 'settings.json')


def _load(path):
    import json
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def current(project_path=None, cfgdir=None):
    """The style in force: project settings shadow user settings."""
    if project_path:
        v = _load(_settings_path(project_path)).get('outputStyle')
        if v:
            return v
    return _load(_settings_path(None, cfgdir)).get('outputStyle') or 'default'


def select(name, project_path=None, cfgdir=None):
    """Write `outputStyle` into the right settings.json, preserving the rest.

    A read-modify-write of a file Claude Code owns: every other key is carried
    through untouched, and `default` clears the key instead of pinning a value
    that is really "no override".
    """
    import json
    path = _settings_path(project_path, cfgdir)
    data = _load(path)
    if name == 'default':
        data.pop('outputStyle', None)
    else:
        data['outputStyle'] = name
    if not _c.write_json_atomic(path, data):
        return False, f'Could not write {path}'
    where = 'this project' if project_path else 'all projects'
    return True, f'Output style for {where}: {name}'


def _slug(name):
    """A style name is a FILENAME, and it arrives off the wire.

    `save` has always slugged it; `read` and `delete` joined it raw, so
    `?name=../../../../Users/mab/Documents/notes` read that file and delete
    removed any .md on the volume. One sanitiser, three callers.
    """
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', str(name or '')).strip('-.')


def read(name, project_path=None, cfgdir=None):
    """The text behind a style — from disk, from a starter, or the explanation
    that a built-in has no file.

    It used to return '' for anything not on disk, and every built-in is not on
    disk, so `view` rendered "(empty)" on the three styles most people have and
    read as a dead button."""
    slug = _slug(name)
    for _scope, d in _dirs(project_path, cfgdir):
        for fn in (f'{slug}.md', f'{slug.lower()}.md'):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                s = _parse(p)
                if s:
                    return s['body']
    for s in STARTERS:
        if s['name'].lower() == (name or '').lower():
            return s['body']
    for n, desc in BUILTIN:
        if n.lower() == (name or '').lower():
            return BUILTIN_NOTE % desc
    return ''


def install_starter(name, project_path=None, cfgdir=None):
    """Write one of claudectl's starters into the user or project scope, from
    where it behaves exactly like a style you wrote — because it now is one."""
    for s in STARTERS:
        if s['name'].lower() == (name or '').lower():
            return save(s['name'], s['description'], s['body'],
                        project_path, cfgdir)
    return False, f'{name} is not a starter'


def active_scope(project_path=None, cfgdir=None):
    """WHERE the active style is pinned: 'project' | 'user' | ''.

    The precedence (a project settings.json shadows the account one) is the
    thing the picker could not express: two files can both name a style and
    only one of them is in force."""
    if project_path and _load(_settings_path(project_path)).get('outputStyle'):
        return 'project'
    if _load(_settings_path(None, cfgdir)).get('outputStyle'):
        return 'user'
    return ''


def save(name, description, body, project_path=None, cfgdir=None):
    """Create or overwrite a custom style."""
    slug = _slug(name)
    if not slug:
        return False, 'A name is required'
    scope_dir = _dirs(project_path, cfgdir)[-1 if project_path else 0][1]
    path = os.path.join(scope_dir, f'{slug}.md')
    text = (f'---\nname: {name}\ndescription: {description}\n---\n\n'
            f'{body.strip()}\n')
    if not _c.write_atomic(path, text):
        return False, f'Could not write {path}'
    return True, f'Saved {slug}.md'


def delete(name, project_path=None, cfgdir=None):
    """Remove a custom style. Built-ins are not on disk and cannot go."""
    if name in [n for n, _d in BUILTIN]:
        return False, f'{name} ships with Claude Code — nothing to delete'
    slug = _slug(name)
    for _scope, d in _dirs(project_path, cfgdir):
        p = os.path.join(d, f'{slug}.md')
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError as e:
                return False, str(e)
            return True, f'Deleted {name}'
    return False, f'{name} not found'
