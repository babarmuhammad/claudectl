"""Skill management — browse, scaffold, AI-generate, edit, delete, and install
Claude Code *skills* (`.claude/skills/<name>/SKILL.md`).

A skill is a directory holding a `SKILL.md` (YAML frontmatter: name,
description, optional allowed-tools) plus optional supporting files. Claude
Code loads each one's body **on demand** (progressive disclosure) — cheaper
context than an always-on CLAUDE.md.

THE SCOPES ARE CLAUDE CODE'S, NOT OURS
--------------------------------------
Claude Code discovers skills in exactly four places, and `inventory()` reports
all four because anything else is a list of files nobody reads:

  - **personal**  `<cfgdir>/skills/<name>/`     — every project of that account
  - **project**   `<project>/.claude/skills/`   — that project only
  - **plugin**    `<plugin>/skills/<name>/`     — namespaced `/plugin:skill`
  - **bundled**   built into Claude Code        — not on disk at all

This module used to offer a fifth, `config.skills_library_dir`
(`~/.claude/claudectl-skills`), as "your library". Nothing reads that path:
copying a skill there looked like installing it and did nothing, which is why
the whole section read as inert. It is now a legacy location that
`migrate_library()` empties into the personal scope once, and "my skills" means
the personal directory Claude Code actually loads.

Two facts from Claude Code's own docs that the UI depends on:
  - the command comes from the DIRECTORY name; for a personal or project skill
    the frontmatter `name` is only a display label (a plugin skill is the
    exception — there `name` sets the last segment).
  - personal overrides project on a name clash, so a shadowed project skill is
    reported as such rather than listed as if it were live.

Bundled skills are NOT enumerated from a hardcoded list — they are whatever
Claude Code's own `skillUsage` counters mention that is not on disk. Inventing
the list is the mistake `checkpoints.py` documents.

Mirrors agents.py so the two managers feel identical.
"""

import os
import re
import shutil

from .config import W, get_claude_exe, open_in_editor
from .ui import (menu, text_input, flash, pause, confirm, multiselect,
                 run_with_progress, pager, _cls)
from . import config as _c
from . import render

# Tools a skill may restrict itself to via `allowed-tools`. Omit to inherit all.
KNOWN_TOOLS = ['Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep',
               'WebFetch', 'WebSearch']


# ── scope dirs ───────────────────────────────────────────────

def bundled_templates_dir():
    """Read-only starter templates shipped inside the package."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skills_templates')


def personal_dir(cfgdir=None):
    """`<account>/skills` — the personal scope Claude Code loads in every
    project. Derived per call: the active account changes under a running
    process, and a module constant would freeze whichever one was current at
    import (the binding bug CLAUDE.md records three times)."""
    return os.path.join(cfgdir or _c.config_dir, 'skills')


def library_dir(cfgdir=None):
    """Where "my skills" live. Kept as a name because half the codebase says
    library; it now points at the personal scope rather than at the private
    directory nothing read."""
    return personal_dir(cfgdir)


def legacy_library_dir():
    """The pre-1.8 private store (`~/.claude/claudectl-skills`). Only
    `migrate_library` and the tests that pin it have any business here."""
    return _c.skills_library_dir


def project_skills_dir(project_path):
    return os.path.join(project_path, '.claude', 'skills')


def skill_md(skill_dir):
    return os.path.join(skill_dir, 'SKILL.md')


# ── frontmatter parse / write ────────────────────────────────

def parse_skill(skill_dir):
    """Return (meta: dict, body: str) from <skill_dir>/SKILL.md. Tolerant of
    missing/malformed frontmatter."""
    path = skill_md(skill_dir)
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            text = f.read()
    except Exception:
        return {}, ''
    meta, body = {}, text
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            fm = text[3:end].strip('\n')
            body = text[end + 4:].lstrip('\n')
            key = None
            for line in fm.splitlines():
                # A YAML folded/literal block (`description: >-`) continues on
                # the indented lines below it. Reading only the first line gave
                # `description` the value '>-', which then read as "this skill
                # has no description" — a warning about the parser, printed as a
                # warning about the user's skill.
                if key and (line.startswith((' ', '\t')) or not line.strip()):
                    meta[key] = (meta[key] + ' ' + line.strip()).strip()
                    continue
                if ':' in line:
                    k, v = line.split(':', 1)
                    key = k.strip()
                    v = v.strip()
                    meta[key] = '' if v in ('>', '>-', '|', '|-') else v
                else:
                    key = None
    return meta, body


def write_skill(skill_dir, meta, body):
    """Write <skill_dir>/SKILL.md with frontmatter. Returns True on success."""
    order = ['name', 'description', 'allowed-tools']
    keys = order + [k for k in meta if k not in order]
    fm = '\n'.join(f"{k}: {meta[k]}" for k in keys if meta.get(k))
    out = f"---\n{fm}\n---\n\n{body.rstrip()}\n"
    try:
        os.makedirs(skill_dir, exist_ok=True)
        with open(skill_md(skill_dir), 'w', encoding='utf-8') as f:
            f.write(out)
        return True
    except Exception:
        return False


def list_skills(scope_dir):
    """[(name, description, skill_dir)] for each subdir that has a SKILL.md."""
    out = []
    if not scope_dir or not os.path.isdir(scope_dir):
        return out
    for n in sorted(os.listdir(scope_dir)):
        d = os.path.join(scope_dir, n)
        if os.path.isfile(skill_md(d)):
            meta, _ = parse_skill(d)
            out.append((meta.get('name', n), meta.get('description', ''), d))
    return out


def list_templates():
    """The bundled starter skills, as install SOURCES. [(name, desc, dir, src)].

    It used to merge the private library in and label the rows `library`, which
    is what made two different things — a starter you can install and a skill
    that is actually loaded — look like one list."""
    return [(name, desc, d, 'template')
            for name, desc, d in list_skills(bundled_templates_dir())]


def _slug(name):
    return re.sub(r'[^a-z0-9-]+', '-', (name or '').lower()).strip('-') or 'skill'


# ── the inventory ────────────────────────────────────────────

def _usage(cfgdir=None):
    """{command: {'uses': n, 'last_used': '2d'}} — how often you TYPED `/name`.

    Merged across EVERY account, not just the one on screen. The counters live
    per account, and a user who works under three logins was being shown a third
    of the truth: on this machine `.claude` has eight skill rows, `.claude-personal`
    three and `.claude-Lorenzo` one.

    This counts explicit invocation and nothing else — see `_activity` for the
    half that explains "caveman: used twice, 56 days ago" on a plugin that runs
    every day."""
    from . import clientstate
    out = {}
    for _name, d in _c.all_config_dirs():
        try:
            rows = clientstate.usage_rollup(d).get('skills') or []
        except Exception:
            continue
        for r in rows:
            cur = out.setdefault(r['name'], {'uses': 0, 'last_used': ''})
            cur['uses'] += r.get('count') or 0
            # the most RECENT wins: '2d' beats '56d', and '' beats nothing
            if not cur['last_used'] or _age_rank(r.get('last_used')) < _age_rank(cur['last_used']):
                cur['last_used'] = r.get('last_used') or cur['last_used']
    return out


_AGE_UNITS = {'now': 0, 'm': 1, 'h': 60, 'd': 1440}


def _age_rank(age):
    """'2d' / '5h' / 'now' → minutes, for picking the most recent of two."""
    a = (age or '').strip()
    if not a:
        return 10 ** 9
    if a == 'now':
        return 0
    try:
        return int(a[:-1]) * _AGE_UNITS.get(a[-1], 1)
    except ValueError:
        return 10 ** 9


def _activity(cfgdir=None):
    """{token: {'sessions': n, 'of': N}} — in how many recent sessions did a
    thing by this name actually RUN.

    The answer to the complaint that started this: `skillUsage` says caveman was
    used twice in 56 days, because it arrives through a SessionStart hook and
    those touch no counter. The transcripts record every hook run, so this is
    measured rather than assumed. Merged across accounts for the same reason the
    counts are."""
    from . import clientstate
    hits, total = {}, 0
    for _name, d in _c.all_config_dirs():
        try:
            got = clientstate.hook_activity(d)
        except Exception:
            continue
        total += got.get('sessions') or 0
        for tok, v in (got.get('hits') or {}).items():
            cur = hits.setdefault(tok, 0)
            hits[tok] = cur + (v.get('sessions') or 0)
    return {'of': total, 'hits': hits}


def _quality(meta):
    """(auto, weak) — the two reasons a skill silently never fires.

    `disable-model-invocation: true` means Claude may never load it on its own,
    only you by typing it. And since Claude picks a skill by matching the task
    against its DESCRIPTION, a missing or one-word description is a skill that
    can only be found by name."""
    auto = str(meta.get('disable-model-invocation', '')).strip().lower() not in ('true', 'yes', '1')
    desc = (meta.get('description') or '').strip()
    return auto, (len(desc) < 25 or len(desc.split()) < 4)


def _row(name, desc, d, scope, command, use, act=None, meta=None, **extra):
    auto, weak = _quality(meta or {})
    r = {'name': name, 'desc': desc, 'dir': d, 'scope': scope,
         'command': command, 'uses': 0, 'last_used': '', 'plugin': '',
         'shadowed': False, 'auto': auto, 'weak': weak, 'via': '',
         'sessions': 0, 'of_sessions': (act or {}).get('of', 0)}
    r.update(use.get(command) or {})
    # Which token proves THIS row ran. The skill's own folder/command name
    # always counts; the PLUGIN's name counts only for the plugin's namesake
    # skill (`caveman:caveman`), because a bundle's hook firing says the bundle
    # is active — not that each of its thirteen skills was used.
    hits = (act or {}).get('hits') or {}
    toks = {command.lower(), os.path.basename(d or '').lower()}
    head, _, tail = command.partition(':')
    if tail and head.lower() == tail.lower():
        toks.add(head.lower())
        r['via'] = head
    for tok in toks:
        r['sessions'] = max(r['sessions'], hits.get(tok, 0))
    r.update(extra)
    return r


def plugin_skills(cfgdir=None):
    """[(plugin_key, skill_name, skill_dir)] for every installed plugin."""
    out = []
    try:
        from . import plugins
        for p in plugins.installed(cfgdir):
            base = os.path.join(p.get('path') or '', 'skills')
            for n in (plugins.contents(p.get('path')).get('skill') or []):
                out.append((p.get('key') or p.get('name', ''), n,
                            os.path.join(base, n)))
    except Exception:
        pass
    return out


def inventory(project_path='', cfgdir=None):
    """Every skill Claude Code can load, by scope, with its real usage.

    The ONE reader behind both managers. Rows carry `command` (what you type),
    because for a personal or project skill that is the directory name and NOT
    the frontmatter `name` the old UI displayed.
    """
    use = _usage(cfgdir)
    act = _activity(cfgdir)
    pdir = personal_dir(cfgdir)
    personal = [_row(name, desc, d, 'personal', os.path.basename(d), use, act,
                     parse_skill(d)[0])
                for name, desc, d in list_skills(pdir)]
    own = {r['command'] for r in personal}

    projdir = project_skills_dir(project_path) if project_path else ''
    project = [_row(name, desc, d, 'project', os.path.basename(d), use, act,
                    parse_skill(d)[0], shadowed=os.path.basename(d) in own)
               for name, desc, d in list_skills(projdir)]

    plugin = []
    for key, n, d in plugin_skills(cfgdir):
        meta, _ = parse_skill(d)
        # a plugin skill IS namespaced by its plugin, and its frontmatter name
        # sets the last segment — the opposite of the personal/project rule
        cmd = '%s:%s' % (key.split('@')[0], meta.get('name') or n)
        plugin.append(_row(meta.get('name') or n, meta.get('description', ''),
                           d, 'plugin', cmd, use, act, meta, plugin=key))

    known = own | {r['command'] for r in project} | {r['command'] for r in plugin}
    bundled = [{'name': cmd, 'desc': '', 'dir': '', 'scope': 'bundled',
                'command': cmd, 'plugin': '', 'shadowed': False,
                'auto': True, 'weak': False, 'sessions': 0,
                'of_sessions': act.get('of', 0), **v}
               for cmd, v in sorted(use.items(), key=lambda kv: -kv[1]['uses'])
               if cmd not in known]

    return {'personal': personal, 'project': project, 'plugin': plugin,
            'bundled': bundled,
            'templates': [{'name': n, 'desc': dsc, 'dir': d, 'scope': 'template',
                           'command': os.path.basename(d), 'uses': 0,
                           'last_used': '', 'plugin': '', 'shadowed': False,
                           'auto': True, 'weak': False, 'sessions': 0,
                           'of_sessions': 0}
                          for n, dsc, d, _src in list_templates()],
            'personal_dir': pdir, 'project_dir': projdir,
            'sessions_scanned': act.get('of', 0)}


def migrate_library(settings=None):
    """Move the pre-1.8 private library into the personal scope, once.

    `~/.claude/claudectl-skills` is a directory Claude Code never reads, so a
    skill saved there was invisible to the tool it was written for. Every
    account gets a copy (what you provision is a property of you, not of
    whichever account was active), a name that already exists is left alone, and
    the source is not deleted — a migration that loses the only copy of
    something the user wrote is not a migration.

    Returns the list of (account_dir, skill_name) it created.
    """
    from .config import load_settings, save_settings
    s = settings if settings is not None else load_settings()
    if s.get('skills_migrated'):
        return []
    src_root = legacy_library_dir()
    done = []
    for _name, cfgdir in _c.all_config_dirs():
        for _n, _d, d in list_skills(src_root):
            dest = os.path.join(personal_dir(cfgdir), os.path.basename(d))
            if os.path.isdir(dest):
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copytree(d, dest)
                done.append((cfgdir, os.path.basename(d)))
            except Exception:
                pass
    s['skills_migrated'] = True
    save_settings(s)
    return done


# ── install / remove ─────────────────────────────────────────

def install_skill(src_dir, dest_root):
    """Copy a skill folder into *dest_root* (a skills directory, not a project).
    Returns the destination dir or ''.

    The destination is explicit because there are now two real answers —
    `personal_dir()` and `project_skills_dir(path)` — and a caller that means
    "everywhere" must not have to know which one that is."""
    if not src_dir or not dest_root or not os.path.isfile(skill_md(src_dir)):
        return ''
    dest = os.path.join(dest_root, os.path.basename(src_dir))
    try:
        os.makedirs(dest_root, exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest)
        return dest
    except Exception:
        return ''


def delete_skill(skill_dir):
    try:
        shutil.rmtree(skill_dir)
        return True
    except Exception:
        return False


def install_from_git(repo_url, project_path, exec_model='', cfgdir=None):
    """Clone a skill+agents bundle from a git repo and install it the way
    its own README documents (e.g. fable-foreman —
    github.com/olsenbrands/fable-foreman, MIT, Jordan Olsen): skills/<name>/
    goes to the normal skill dest (project if given, else the user library);
    agents/*.md go to Claude Code's OWN global agent dir (<config_dir>/agents)
    so Claude auto-discovers them directly — deliberately NOT claudectl's
    agents_library_dir, which is excluded from auto-discovery on purpose.

    If exec_model is set, any agent frontmatter pinning `model: <id>` is
    rewritten to it — a subagent inherits the parent session's
    ANTHROPIC_BASE_URL (see config.omniroute_env), so a hardcoded model name
    like "sonnet" would otherwise be requested from whatever free-tier proxy
    is configured instead of the real API.

    Returns (ok, message). Never raises — network/git failures become a
    message, not a crash.
    """
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp(prefix='claudectl-skill-')
    try:
        from . import proc
        # BEFORE the clone, not with the review gate below it: the gate protects
        # what gets copied out of the checkout, and `git clone ext::sh -c …`
        # never reaches it — the payload has already run.
        if not proc.remote_url_ok(repo_url):
            return False, ('not a git remote URL — use https://, ssh:// or '
                           'git@host:owner/repo')
        r = proc.run(['git', 'clone', '--depth', '1', '--', repo_url, tmp],
                     timeout=60)
        if r is None:
            return False, 'git not available'
        if r.returncode != 0:
            return False, f'git clone failed: {(r.stderr or "").strip()[:200]}'

        # ── review gate ──────────────────────────────────────────────────
        # Build the full plan BEFORE writing anything, so the user is shown the
        # real operation rather than a description of it, and so a rejection
        # leaves the disk untouched. See skillscan for why this exists and,
        # more importantly, what it does not promise.
        skills_src = os.path.join(tmp, 'skills')
        agents_src = os.path.join(tmp, 'agents')
        agents_dest_dir = os.path.join(_c.resolve_config_dir(cfgdir), 'agents')
        plan, skill_dirs, agent_files = [], [], []
        if os.path.isdir(skills_src):
            for name in sorted(os.listdir(skills_src)):
                d = os.path.join(skills_src, name)
                if not os.path.isfile(skill_md(d)):
                    continue
                skill_dirs.append((name, d))
                dest_root = os.path.join(
                    project_skills_dir(project_path) if project_path
                    else personal_dir(cfgdir), name)
                for base, _dirs, files in os.walk(d):
                    for fn in sorted(files):
                        full = os.path.join(base, fn)
                        rel = os.path.relpath(full, tmp).replace('\\', '/')
                        sub = os.path.relpath(full, d)
                        plan.append((rel, os.path.join(dest_root, sub), 'skill'))
        if os.path.isdir(agents_src):
            for fn in sorted(os.listdir(agents_src)):
                if not fn.endswith('.md'):
                    continue
                agent_files.append(fn)
                plan.append((f'agents/{fn}', os.path.join(agents_dest_dir, fn), 'agent'))

        if not plan:
            return False, 'No skills/ or agents/ folder found in that repo'
        from . import skillscan
        if not skillscan.review_gate(tmp, plan, source=repo_url):
            return False, 'Cancelled — nothing was installed'

        installed = []
        for name, d in skill_dirs:
            dest = install_skill(d, project_skills_dir(project_path) if project_path
                                 else personal_dir(cfgdir))
            if dest:
                installed.append(name)

        agent_count = 0
        if agent_files:
            agents_dest = agents_dest_dir
            os.makedirs(agents_dest, exist_ok=True)
            for fn in agent_files:
                with open(os.path.join(agents_src, fn), encoding='utf-8') as f:
                    text = f.read()
                if exec_model:
                    text = re.sub(r'(?m)^model:\s*\S+', f'model: {exec_model}', text)
                with open(os.path.join(agents_dest, fn), 'w', encoding='utf-8') as f:
                    f.write(text)
                agent_count += 1

        if not installed and not agent_count:
            return False, 'No skills/ or agents/ folder found in that repo'
        return True, f'Installed {len(installed)} skill(s), {agent_count} agent(s)'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_personal(src_dir, all_accounts=True):
    """Install a skill into the personal scope of EVERY account. [(name, dir)].

    A skill you wrote is a property of you, not of whichever login happened to
    be active when you saved it — the same rule the hook fan-out and
    `sync-accounts` already follow. Without this, "personal" quietly meant "one
    account", and a skill vanished the moment you switched.
    """
    out = []
    for name, cfgdir in (_c.all_config_dirs() if all_accounts
                         else [('', _c.config_dir)]):
        dest = install_skill(src_dir, personal_dir(cfgdir))
        if dest:
            out.append((name, dest))
    return out


def delete_personal(skill_dir, all_accounts=True):
    """Remove a personal skill from every account that has it. [(name, dir)].

    The other half of the fan-out: installing everywhere and deleting in one
    place turns "installed" into "installed in four accounts you forgot about".
    """
    base = os.path.basename(skill_dir)
    gone = []
    for name, cfgdir in (_c.all_config_dirs() if all_accounts
                         else [('', _c.config_dir)]):
        d = os.path.join(personal_dir(cfgdir), base)
        if os.path.isdir(d) and delete_skill(d):
            gone.append((name, d))
    return gone


def personal_accounts(skill_dir):
    """Which accounts already have a personal skill by this name — what the
    confirmation dialog needs in order to say what it is about to do."""
    base = os.path.basename(skill_dir)
    return [name for name, cfgdir in _c.all_config_dirs()
            if os.path.isfile(skill_md(os.path.join(personal_dir(cfgdir), base)))]


def save_to_library(src_dir, cfgdir=None):
    """Copy a template, project or plugin skill into the PERSONAL scope.

    Kept under its old name (a route and sixteen call sites say "library") but
    it no longer copies into a directory nothing reads — and it reaches every
    account, because that is what "available everywhere" has to mean."""
    done = install_personal(src_dir)
    return done[0][1] if done else ''


# ── TUI menu ─────────────────────────────────────────────────

#: what each scope means, in the order Claude Code resolves them. The label is
#: the answer to "am I using this?", which is the question the old screen — a
#: list of templates and a private library — could not answer at all.
SCOPE_LABELS = [
    ('personal', 'personal — every project on this account'),
    ('project',  'this project only'),
    ('plugin',   'from plugins — namespaced, managed on the Plugins screen'),
    ('bundled',  'built into Claude Code — seen in your usage'),
]


def _use_tail(r):
    if r.get('uses'):
        return f"used {r['uses']}x" + (f" · {r['last_used']}" if r.get('last_used') else '')
    return 'never used'


def skills_menu(project_path=None):
    """Every skill Claude Code can load, by scope, plus the starters to install.

    `menu()` already filters as you type, so there is no search row here."""
    while True:
        inv = inventory(project_path or '')
        items = []
        for scope, label in SCOPE_LABELS:
            rows = inv[scope]
            if scope == 'project' and not project_path:
                continue
            items.append((f"{_c.C_DIM}── {label}  ({len(rows)}) ──{_c.C_RESET}", None))
            if not rows:
                items.append((f"   {_c.C_DIM}(none){_c.C_RESET}", None))
            for r in rows:
                tail = f"  {_c.C_DIM}{render.trunc(r['desc'], 34)}{_c.C_RESET}" if r['desc'] else ''
                mark = f"  {_c.C_WARN}shadowed by personal{_c.C_RESET}" if r['shadowed'] else ''
                items.append((f"/{r['command']:<26}{_c.C_DIM}{_use_tail(r):<18}{_c.C_RESET}{tail}{mark}",
                              (f"{scope}:{r['dir']}" if r['dir'] else None)))
        tmpls = inv['templates']
        if tmpls:
            items.append((f"{_c.C_DIM}── starters you can install ({len(tmpls)}) ──{_c.C_RESET}", None))
            for r in tmpls:
                tail = f"  {_c.C_DIM}{render.trunc(r['desc'], 40)}{_c.C_RESET}" if r['desc'] else ''
                items.append((f"{r['name']}{tail}", f"template:{r['dir']}"))
        items += [(f"{'─' * W}", None),
                  ('＋  New skill (manual)', '__new__'),
                  ('✦  New skill (AI-generated)', '__ai__'),
                  ('🌐  Install skill+agents from GitHub…', '__git__')]

        sel = menu(items, "SKILLS  /  " + (os.path.basename(project_path) if project_path
                                           else 'personal'))
        if not sel:
            return
        if sel == '__new__':
            _new_skill_manual(project_path)
        elif sel == '__ai__':
            _new_skill_ai(project_path)
        elif sel == '__git__':
            _new_skill_from_git(project_path)
        else:
            scope, _, skill_dir = sel.partition(':')
            _skill_detail(skill_dir, scope, project_path)


def view_skill_file(skill_dir):
    """Read-only pager over a skill's raw SKILL.md."""
    path = skill_md(skill_dir)
    if not os.path.isfile(path):
        flash("SKILL.md not found", ok=False, secs=1.2)
        return
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        flash(f"Read failed: {e}", ok=False, secs=1.4)
        return
    w = render.content_width()
    lines = []
    for raw in text.replace('\t', '    ').split('\n'):
        if not raw:
            lines.append(''); continue
        while len(raw) > w - 4:
            cut = raw.rfind(' ', 0, w - 4)
            cut = cut if cut > 0 else w - 4
            lines.append(raw[:cut]); raw = raw[cut:].lstrip()
        lines.append(raw)
    pager(('CLAUDECTL', os.path.basename(skill_dir), 'SKILL'), lines)


def _skill_detail(skill_dir, scope, project_path):
    """ONE detail screen for every scope. What it offers follows from where the
    skill lives — a plugin's copy is not ours to edit or delete, and a starter
    is not installed anywhere yet."""
    meta, _ = parse_skill(skill_dir)
    cmd = os.path.basename(skill_dir)
    name = meta.get('name', cmd)
    where = {'personal': personal_dir(), 'project': 'this project',
             'plugin': 'a plugin', 'template': 'a bundled starter'}.get(scope, scope)
    items = [
        (f"Command     :  /{cmd}", None),
        (f"Name        :  {name}", None),
        (f"Description :  {render.trunc(meta.get('description', ''), W - 18)}", None),
        (f"Where       :  {render.trunc(str(where), W - 18)}", None),
        (f"{'─' * W}", None),
        ('👁  View SKILL.md', 'view'),
    ]
    if scope in ('personal', 'project'):
        items.append(('📝  Edit in editor', 'edit'))
    if scope in ('template', 'project', 'plugin'):
        items.append(('👤  Copy to personal — every project', 'personal'))
    if project_path and scope in ('template', 'personal', 'plugin'):
        items.append(('📁  Copy into this project', 'project'))
    if scope in ('personal', 'project'):
        items.append(('🗑  Delete', 'delete'))
    sel = menu(items, f"SKILL  /  {name}")
    if sel == 'view':
        view_skill_file(skill_dir)
    elif sel == 'edit':
        open_in_editor(skill_md(skill_dir))
    elif sel == 'personal':
        dest = save_to_library(skill_dir)
        flash("Copied to personal skills" if dest else "Copy failed",
              ok=bool(dest), secs=1.4)
    elif sel == 'project':
        dest = install_skill(skill_dir, project_skills_dir(project_path))
        flash(f"Installed → {os.path.basename(dest)}" if dest else "Install failed",
              ok=bool(dest), secs=1.4)
    elif sel == 'delete':
        if confirm(f"Delete skill '{cmd}' from {scope} skills?", danger=True):
            flash("Deleted" if delete_skill(skill_dir) else "Delete failed",
                  ok=True, secs=1.2)


def _dest_dir(project_path):
    """Where a newly-created skill lands: the project when there is one, else
    the personal scope Claude Code reads for every project."""
    return project_skills_dir(project_path) if project_path else personal_dir()


def _new_skill_manual(project_path):
    name = text_input("Skill name (e.g. commit-message):")
    if not name:
        return
    desc = text_input("Description — when should Claude use this skill?:") or ''
    tools = multiselect([(t, t) for t in KNOWN_TOOLS],
                        "ALLOWED TOOLS (none = inherit all)")
    if tools is None:
        return
    meta = {'name': _slug(name), 'description': desc}
    if tools:
        meta['allowed-tools'] = ', '.join(t for t in KNOWN_TOOLS if t in tools)
    body = (f"# {name}\n\n{desc}\n\n"
            f"## Instructions\n\n"
            f"1. \n2. \n\n"
            f"## Notes\n\n- \n")
    skill_dir = os.path.join(_dest_dir(project_path), _slug(name))
    if os.path.isdir(skill_dir) and not confirm(f"'{_slug(name)}' exists — overwrite?"):
        return
    if write_skill(skill_dir, meta, body):
        flash(f"Created {_slug(name)}/SKILL.md")
        open_in_editor(skill_md(skill_dir))
    else:
        flash("Write failed", ok=False, secs=1.4)


def _new_skill_from_git(project_path):
    url = text_input(
        "Git URL of the skill+agents repo:",
        default='https://github.com/olsenbrands/fable-foreman')
    if not url:
        return
    exec_model = _c.load_settings().get('omniroute_exec_model', '')
    _cls()
    print(f"\n  Cloning {url} ...\n")
    ok, msg = install_from_git(url, project_path, exec_model)
    flash(msg, ok=ok, secs=2.2)


def build_ai_prompt(name, role, project_path):
    """The authoring prompt for AI skill generation. Shared by the TUI flow and
    the GUI job so both produce identical output."""
    from .claude_md import _build_ai_context
    ctx = _build_ai_context(project_path, None) if project_path else ''
    return (
        f"Author a Claude Code SKILL.md for a skill named '{_slug(name)}'.\n"
        f"Purpose: {role}\n\n"
        + (f"PROJECT CONTEXT:\n{ctx}\n\n" if ctx else "")
        + "A skill is instructions Claude loads on demand. Output EXACTLY this "
        "shape and nothing else:\n"
        "---\n"
        f"name: {_slug(name)}\n"
        "description: <one sentence written so Claude knows WHEN to use this "
        "skill — mention the trigger conditions and keywords>\n"
        "---\n\n"
        "# <Title>\n\n"
        "<concise, actionable markdown instructions: what to do, step by step, "
        "with any conventions or examples. Keep it tight — this is loaded into "
        "context when triggered.>\n\n"
        "Do NOT create or write any files and do not use any tools — return the "
        "markdown directly. No preamble, no code fences."
    )


def write_skill_raw(dest_root, name, content):
    """Write approved AI-generated markdown as <dest_root>/<slug>/SKILL.md.
    Returns the skill dir on success, '' on failure. Used by the GUI job."""
    skill_dir = os.path.join(dest_root, _slug(name))
    try:
        os.makedirs(skill_dir, exist_ok=True)
        with open(skill_md(skill_dir), 'w', encoding='utf-8') as f:
            f.write(content if content.endswith('\n') else content + '\n')
        return skill_dir
    except Exception:
        return ''


def _new_skill_ai(project_path):
    claude = get_claude_exe()
    if not claude:
        _cls(); print("\n  claude.exe not found.\n"); pause("  Press Enter..."); return
    name = text_input("Skill name (e.g. changelog-writer):")
    if not name:
        return
    role = text_input("What should this skill do? (one line):") or name

    from .claude_md import _pager_confirm
    from .memory import extract_model
    prompt = build_ai_prompt(name, role, project_path)
    _mf = ['--model', extract_model()] if extract_model() else []
    out, cancelled = run_with_progress(
        [claude, *_mf, '--print', prompt, '--disallowedTools', 'Write,Edit,NotebookEdit,Bash'],
        ('CLAUDECTL', 'SKILLS', _slug(name)),
        f'Authoring skill {_slug(name)} with Claude...  (15-60s)', timeout=120)
    if cancelled:
        flash("Cancelled", ok=False); return
    content = (out or '').strip()
    if not content:
        from .memory import why_failed
        flash(why_failed(), ok=False, secs=2.4); return
    if not _pager_confirm(f"SKILL  /  {_slug(name)}  — approve to write", content):
        _cls(); print("\n  Rejected — not written.\n"); pause("  Press Enter..."); return
    if write_skill_raw(_dest_dir(project_path), name, content):
        flash(f"Created {_slug(name)}/SKILL.md")
        open_in_editor(skill_md(os.path.join(_dest_dir(project_path), _slug(name))))
    else:
        flash("Write failed", ok=False, secs=1.6)
