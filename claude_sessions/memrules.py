"""Compile the semantic memory graph into path-scoped Claude Code rules.

Each repo/module unit becomes <project>/.claude/rules/claudectl-mem-*.md with a
`globs:` frontmatter — Claude Code loads the rule ONLY when it touches matching
files. Zero always-on token cost; per-module detail appears exactly when
relevant. Prunes only its own (prefix-scoped) files — user rules are never
touched.
"""

import os
import re

from .memory import tokens_estimate

RULE_PREFIX = 'claudectl-mem-'
RULE_MAX_TOKENS = 600


def _sanitize(s):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', s or '').strip('_') or 'root'


def rule_filename(repo, module):
    return f"{RULE_PREFIX}{_sanitize(repo)}-{_sanitize(module)}.md"


def _unit_glob(entities):
    """Project-relative glob for a unit, derived from its entities' files."""
    dirs = set()
    for e in entities:
        for f in e.get('source_files', []) or []:
            d = os.path.dirname(str(f).replace('\\', '/'))
            if d:
                dirs.add(d)
    if not dirs:
        return '**'
    common = os.path.commonprefix([d + '/' for d in dirs]).rsplit('/', 1)[0]
    return f"{common}/**" if common else '**'


def render_rule(repo, module, summary, entities, relations):
    glob = _unit_glob(entities)
    lines = [
        '---',
        f'description: "claudectl memory: {repo}/{module}"',
        f'globs: "{glob}"',
        '---',
        f"# {repo}/{module}" + (f" — {summary}" if summary else ''),
    ]
    for e in entities:
        s = (e.get('summary') or '').strip()
        lines.append(f"- {e.get('name')} ({e.get('type', '')})" + (f" — {s}" if s else ''))
    names = {e.get('name') for e in entities}
    rels = [f"{r['source']} {r.get('rel', 'relates')} {r['target']}"
            for r in relations if r.get('source') in names and r.get('target') in names]
    if rels:
        lines.append("Relations: " + '; '.join(rels))
    text = '\n'.join(lines)
    while tokens_estimate(text) > RULE_MAX_TOKENS and len(lines) > 6:
        lines.pop(-2 if rels else -1)
        text = '\n'.join(lines)
    return text + '\n'


def sync_rules(project_path, proj_folder, mem):
    """Write one rule per unit with >=2 entities; prune stale claudectl-mem-*
    files. Returns list of written filenames. Best-effort."""
    from .config import load_settings
    if not load_settings().get('memory_rules', True):
        return []
    rules_dir = os.path.join(project_path, '.claude', 'rules')
    by_unit = {}
    for e in mem.get('entities', []):
        if e.get('type') == 'lesson' or not e.get('valid', True):
            continue
        by_unit.setdefault((e.get('repo', ''), e.get('module', '')), []).append(e)

    written = []
    try:
        os.makedirs(rules_dir, exist_ok=True)
        keep = set()
        for (repo, module), ents in by_unit.items():
            if len(ents) < 2:
                continue
            name = rule_filename(repo, module)
            keep.add(name)
            summary = (mem.get('summaries', {}) or {}).get(f"{repo}/{module}", '')
            body = render_rule(repo, module, summary, ents, mem.get('relations', []))
            p = os.path.join(rules_dir, name)
            old = ''
            if os.path.isfile(p):
                try:
                    old = open(p, encoding='utf-8', errors='ignore').read()
                except Exception:
                    old = ''
            if old != body:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(body)
            written.append(name)
        # prune ONLY our own stale files
        for nm in os.listdir(rules_dir):
            if nm.startswith(RULE_PREFIX) and nm not in keep:
                try:
                    os.remove(os.path.join(rules_dir, nm))
                except OSError:
                    pass
    except Exception:
        from . import config as _c
        _c.log.exception('memrules: sync failed')
    return written
