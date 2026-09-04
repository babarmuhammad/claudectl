"""Claude Code plugins and marketplaces.

A plugin is now the canonical unit of distribution: a versioned bundle shipping
any of skills, subagents, slash commands, hooks, output styles and MCP servers
together. claudectl manages every one of those individually and, until this
module, could not see the bundle that contained them.

THE PART THAT ONLY CLAUDECTL CAN DO
-----------------------------------
Listing plugins is table stakes — `/plugin` already does it. What no other tool
is positioned to show is PROVENANCE: claudectl's agent, skill and hook managers
present a flat list, so there is no way to tell what you installed deliberately
from what a bundle brought along. After a few marketplace installs that list is
a mystery, and the only safe-looking action — delete it — may break a plugin.

`provenance_index()` is therefore the point of this file. The rest is plumbing.

ON-DISK FORMAT
--------------
Read from the live files, not from the documentation, which describes a
`marketplaces.json` that this version does not write:

  ~/.claude/plugins/known_marketplaces.json   name -> {source, installLocation}
  ~/.claude/plugins/installed_plugins.json    {"version":2, "plugins": {
                                                 "<plugin>@<marketplace>": [
                                                   {scope, installPath, version,
                                                    installedAt, gitCommitSha}]}}
  ~/.claude/plugins/marketplaces/<name>/       the cloned marketplace
  ~/.claude/plugins/cache/<mkt>/<plugin>/<v>/  the installed plugin itself

Both files are read defensively and treated as advisory: they belong to Claude
Code, the shape has already changed once, and claudectl showing a stale row is
strictly better than claudectl crashing on an unfamiliar key.
"""

import json
import os
import re

from . import config as _c

#: what a plugin can ship, and the directory each lives in. Order is the order
#: the UI lists them in.
KINDS = (('skills', 'skill'), ('agents', 'agent'),
         ('commands', 'command'), ('hooks', 'hook'))

#: filenames that live alongside a plugin's content without being content
_NOT_CONTENT = {'readme', 'license', 'licence', 'package', 'package-lock',
                'changelog', 'contributing', '.gitignore', 'tsconfig'}


def plugins_dir(cfg_dir=None):
    return os.path.join(cfg_dir or _c.config_dir, 'plugins')


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8-sig') as f:
            d = json.load(f)
        return d if isinstance(d, (dict, list)) else default
    except Exception:
        return default


def known_marketplaces(cfg_dir=None):
    """[{name, repo, source, path, updated}] — registered marketplaces."""
    raw = _read_json(os.path.join(plugins_dir(cfg_dir), 'known_marketplaces.json'), {})
    out = []
    for name, v in (raw.items() if isinstance(raw, dict) else []):
        src = (v or {}).get('source') or {}
        out.append({
            'name': name,
            'source': src.get('source', ''),
            'repo': src.get('repo') or src.get('url') or '',
            'path': (v or {}).get('installLocation', ''),
            'updated': (v or {}).get('lastUpdated', ''),
        })
    out.sort(key=lambda r: r['name'].lower())
    return out


def installed(cfg_dir=None):
    """[{key, name, marketplace, scope, version, path, installed_at, sha}].

    The key is `<plugin>@<marketplace>` and a plugin may legitimately appear
    more than once (different scopes), so each install is its own row rather
    than being collapsed — collapsing would hide a user-scope plugin shadowing
    a project-scope one, which is exactly the kind of thing you open this list
    to find out.
    """
    raw = _read_json(os.path.join(plugins_dir(cfg_dir), 'installed_plugins.json'), {})
    out = []
    for key, entries in ((raw.get('plugins') or {}).items()
                         if isinstance(raw, dict) else []):
        name, _, mkt = str(key).partition('@')
        for e in (entries if isinstance(entries, list) else [entries]):
            e = e or {}
            out.append({
                'key': key, 'name': name, 'marketplace': mkt,
                'scope': e.get('scope', ''),
                'version': str(e.get('version', ''))[:12],
                'path': e.get('installPath', ''),
                'installed_at': e.get('installedAt', ''),
                'sha': str(e.get('gitCommitSha', ''))[:12],
            })
    out.sort(key=lambda r: (r['marketplace'].lower(), r['name'].lower()))
    return out


def contents(install_path):
    """{kind: [names]} — what a plugin actually places.

    Directory listing, not a manifest read: the manifest declares intent and the
    directory is the truth, and provenance is only useful if it matches what is
    really on disk.
    """
    out = {}
    for folder, kind in KINDS:
        d = os.path.join(install_path or '', folder)
        if not os.path.isdir(d):
            continue
        names = []
        try:
            for fn in sorted(os.listdir(d)):
                full = os.path.join(d, fn)
                stem = os.path.splitext(fn)[0]
                # packaging files sit in these folders too. Listing README and
                # package as if they were hooks makes the provenance index lie,
                # and a wrong provenance label is worse than none — it is the
                # one thing this list exists to be trusted about.
                if stem.lower() in _NOT_CONTENT:
                    continue
                if os.path.isdir(full):
                    names.append(fn)                       # skills/<name>/
                elif fn.lower().endswith(('.md', '.json')):
                    names.append(stem)                     # agents/<name>.md
        except Exception:
            continue
        if names:
            out[kind] = names
    if os.path.isfile(os.path.join(install_path or '', '.mcp.json')):
        out['mcp'] = ['(mcp servers)']
    return out


def provenance_index(cfg_dir=None):
    """{kind: {name: plugin_key}} — "where did this come from?".

    THE reason this module exists. The agent, skill and hook managers show flat
    lists; without this a user cannot tell their own work from a bundle's, and
    the obvious action on something unrecognised — delete it — may quietly break
    a plugin.

    Matching is by name because that is what the managers display and what the
    filesystem gives them. Two plugins shipping the same skill name is a real
    collision; last-writer-wins here mirrors what Claude Code itself does, and
    the row is still labelled, which is the point.
    """
    idx = {}
    for p in installed(cfg_dir):
        for kind, names in contents(p['path']).items():
            bucket = idx.setdefault(kind, {})
            for n in names:
                bucket[n] = p['key']
    return idx


def summary(cfg_dir=None):
    """One payload for the GUI: marketplaces, installs, and what each ships."""
    mkts = known_marketplaces(cfg_dir)
    inst = installed(cfg_dir)
    for p in inst:
        p['provides'] = contents(p['path'])
        p['missing'] = not (p['path'] and os.path.isdir(p['path']))
    return {'marketplaces': mkts, 'plugins': inst,
            'dir': plugins_dir(cfg_dir)}


# ── mutations ────────────────────────────────────────────────
# Delegated to the `claude` CLI rather than reimplemented. These files are
# Claude Code's: it resolves marketplace sources, verifies manifests, handles
# scopes and updates its own caches. Writing them directly would work until the
# format moved — which it already has once — and would then corrupt the state of
# the tool claudectl exists to support.

def _claude_cli(args, timeout=120, cfgdir=None):
    """Run `claude <args>` against ONE account.

    The env is the whole point. Without it the CLI lands on whatever
    CLAUDE_CONFIG_DIR claudectl inherited — normally unset, i.e. the default
    account — while every reader in this module resolves `cfgdir`. Read and
    write then named different accounts: with claudectl switched to another
    account the Plugins page listed that account's plugins and Install wrote
    into default.
    """
    from . import proc
    exe = _c.get_claude_exe()
    if not exe:
        return False, 'claude.exe not found'
    r = proc.run([exe] + list(args), env=_c.account_env(cfgdir), timeout=timeout)
    if r is None:
        return False, 'could not run claude'
    out = ((r.stdout or '') + (r.stderr or '')).strip()
    return r.returncode == 0, out[:400]


def add_marketplace(source, cfgdir=None):
    """`claude plugin marketplace add <repo|url|path>`.

    The three shapes the CLI documents are the three shapes accepted, because
    this value is fetched by git underneath: a bare `ext::sh -c …` is a real git
    transport that executes on clone, and a value starting `-` lands in an
    option position. Neither needs a shell to be a problem.
    """
    from . import proc
    source = (source or '').strip()
    if not source:
        return False, 'No source given'
    if not (re.match(r'^[\w.-]+/[\w.-]+$', source)         # owner/repo
            or proc.remote_url_ok(source)                  # a git remote URL
            or os.path.isdir(source)):                     # a local marketplace
        return False, 'not an owner/repo, a git URL, or a directory that exists'
    return _claude_cli(['plugin', 'marketplace', 'add', source], cfgdir=cfgdir)


def remove_marketplace(name, cfgdir=None):
    return _claude_cli(['plugin', 'marketplace', 'remove', name], cfgdir=cfgdir)


def install_plugin(name, marketplace='', cfgdir=None):
    """Install, after the same review gate third-party skills go through.

    A plugin ships agents and hooks straight into the auto-discovery surfaces,
    so it is the same exposure as `install_from_git` with more moving parts.
    The gate runs on the MARKETPLACE CLONE, which is already on disk — so the
    contents are reviewable before anything is installed from them.
    """
    spec = f'{name}@{marketplace}' if marketplace else name
    return _claude_cli(['plugin', 'install', spec], cfgdir=cfgdir)


def remove_plugin(key, cfgdir=None):
    return _claude_cli(['plugin', 'uninstall', key], cfgdir=cfgdir)


def review_plugin(name, marketplace, cfg_dir=None):
    """Scan a marketplace's copy of a plugin before installing it.

    Returns True to proceed. Reuses skillscan, so the report, the wording and
    the approval gate are identical to the git-bundle path — one review screen,
    not two that drift.
    """
    from . import skillscan
    mkt = next((m for m in known_marketplaces(cfg_dir)
                if m['name'] == marketplace), None)
    root = ''
    for cand in ([os.path.join(mkt['path'], 'plugins', name)] if mkt and mkt['path'] else []):
        if os.path.isdir(cand):
            root = cand
            break
    if not root:
        # nothing local to inspect — say so rather than implying it was checked
        from . import diffview
        return bool(diffview.confirm(
            '', f'{name}@{marketplace}\n\nThis plugin is not cloned locally yet, so '
                'nothing could be inspected before installing.\n\nInstall only from a '
                'source you would give your shell to.',
            'Install without review?'))
    plan = []
    for base, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(base, fn)
            rel = os.path.relpath(full, root).replace('\\', '/')
            kind = 'agent' if rel.startswith('agents/') else 'skill'
            plan.append((rel, f'(plugin {name}@{marketplace}) {rel}', kind))
    return skillscan.review_gate(root, plan, source=f'{name}@{marketplace}')
