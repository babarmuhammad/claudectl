"""Task-scoped memory retrieval — the engine behind claudectl's token-efficient
injection. Scores the semantic graph against a query (IDF keyword overlap +
path match + dependency rank + relation expansion) and assembles a context
string cut to a hard token budget. Pure local — no Claude call, fast enough
for a per-prompt hook (<1s).

IMPORTANT: no ui/render imports at module level — this runs in the
UserPromptSubmit hook where latency matters.
"""

import os
import re
import math
import json

from . import memory


def tokens_estimate(text):
    return max(1, len(text or '') // 4)


_WORD = re.compile(r'[a-z0-9]+')
_CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')


def _tokenize(s):
    """Word set incl. camelCase/snake_case splits: 'UserPromptHook' →
    {userprompthook, user, prompt, hook}."""
    if not s:
        return set()
    out = set(_WORD.findall(s.lower()))
    for part in _CAMEL.sub(' ', s).replace('_', ' ').split():
        out.update(_WORD.findall(part.lower()))
    return out


# ── index ────────────────────────────────────────────────────

def build_index(mem):
    ents = mem.get('entities', [])
    n = max(1, len(ents))
    df = {}
    toks = []
    for e in ents:
        t = _tokenize(e.get('name', '')) | _tokenize(e.get('summary', ''))
        toks.append(t)
        for tok in t:
            df[tok] = df.get(tok, 0) + 1
    idf = {tok: math.log(1 + n / c) for tok, c in df.items()}

    rel_adj = {}
    for r in mem.get('relations', []):
        s, t = r.get('source'), r.get('target')
        if s and t:
            rel_adj.setdefault(s, []).append((t, r.get('rel', 'relates')))
            rel_adj.setdefault(t, []).append((s, r.get('rel', 'relates')))

    mod_adj = {}
    for e in mem.get('module_edges', []):
        s, t = e.get('source'), e.get('target')
        if s and t:
            mod_adj.setdefault(s, []).append(t)
            mod_adj.setdefault(t, []).append(s)

    return {'idf': idf, 'ent_tokens': toks, 'rel_adj': rel_adj, 'mod_adj': mod_adj}


def _path_segments(e):
    segs = set()
    for p in e.get('source_files', []) or []:
        for part in str(p).replace('\\', '/').split('/'):
            segs.add(part.lower())
            stem = os.path.splitext(part)[0].lower()
            segs.add(stem)
    for part in str(e.get('module', '')).split('/'):
        if part:
            segs.add(part.lower())
    return segs


def score_entities(mem, index, query, path_hints=()):
    """[(score, entity)] descending, zero-score dropped."""
    qtok = _tokenize(query)
    hints = {h.lower() for h in path_hints}
    idf = index['idf']
    out = []
    for e, etok in zip(mem.get('entities', []), index['ent_tokens']):
        if not e.get('valid', True):
            continue                                  # superseded fact — history only
        if e.get('type') == 'lesson' and e.get('status') not in ('approved', 'pinned'):
            continue                                  # pending never leaves the TUI
        ntok = _tokenize(e.get('name', ''))
        kw = (sum(idf.get(t, 0) for t in qtok & ntok) * 3.0
              + sum(idf.get(t, 0) for t in qtok & (etok - ntok)) * 1.0)
        segs = _path_segments(e)
        path = 4.0 if (qtok & segs) or (hints & segs) else 0.0
        score = kw + path
        if score <= 0:
            continue
        score += 0.5 * math.log2(1 + e.get('rank', 0))    # tie-break by dep-degree
        if e.get('type') == 'lesson':
            score += 2.0
        out.append((score, e))
    out.sort(key=lambda x: (-x[0], x[1].get('name', '')))
    return out


def expand_relations(mem, index, seeds, hops=1, decay=0.5):
    """Neighbors of the seed entities via entity relations AND module edges,
    inheriting seed_score*decay per hop. Returns [(score, entity)] for NEW
    entities only (dedup keep-max)."""
    by_name = {}
    for e in mem.get('entities', []):
        by_name.setdefault(e.get('name'), e)
    unit_ents = {}
    for e in mem.get('entities', []):
        unit_ents.setdefault(f"{e.get('repo')}/{e.get('module')}", []).append(e)

    seen = {e.get('name') for _s, e in seeds}
    found = {}
    frontier = list(seeds)
    for _hop in range(hops):
        nxt = []
        for s, e in frontier:
            inherit = s * decay
            for other, _rel in index['rel_adj'].get(e.get('name'), []):
                oe = by_name.get(other)
                if oe is None or other in seen:
                    continue
                if oe.get('type') == 'lesson' and oe.get('status') not in ('approved', 'pinned'):
                    continue
                if inherit > found.get(other, (0, None))[0]:
                    found[other] = (inherit, oe)
                nxt.append((inherit, oe))
            unit = f"{e.get('repo')}/{e.get('module')}"
            for nunit in index['mod_adj'].get(unit, []):
                for oe in unit_ents.get(nunit, [])[:3]:   # top few from linked module
                    name = oe.get('name')
                    if name in seen or oe.get('type') == 'lesson':
                        continue
                    w = inherit * 0.5
                    if w > found.get(name, (0, None))[0]:
                        found[name] = (w, oe)
        frontier = nxt
        seen |= set(found)
    return sorted(found.values(), key=lambda x: -x[0])


# ── assembly ─────────────────────────────────────────────────

_HEADER = "PROJECT MEMORY (claudectl) — task-relevant subset:"


def render_context(scored, mem, budget_tokens):
    """Compact context string cut to budget. Relations only among included
    entities; approved lessons under their own header."""
    if not scored:
        return '', 0
    lines = [_HEADER]
    used = tokens_estimate(_HEADER)
    included = []
    lessons = []
    for s, e in scored:
        if e.get('type') == 'lesson':
            line = f"! {e.get('name')}: {e.get('summary', '')}".rstrip()
        else:
            files = ', '.join((e.get('source_files') or [])[:2])
            line = (f"{e.get('module', '')}/{e.get('name')} ({e.get('type', '')}): "
                    f"{e.get('summary', '')}" + (f" [files: {files}]" if files else ''))
        cost = tokens_estimate(line)
        if used + cost > budget_tokens:
            break
        used += cost
        if e.get('type') == 'lesson':
            lessons.append(line)
        else:
            lines.append(line)
        included.append(e.get('name'))

    inc = set(included)
    rel_lines = []
    for r in mem.get('relations', []):
        if r.get('source') in inc and r.get('target') in inc:
            rl = f"{r['source']} -{r.get('rel', 'relates')}-> {r['target']}"
            cost = tokens_estimate(rl)
            if used + cost > budget_tokens:
                break
            used += cost
            rel_lines.append(rl)
    if rel_lines:
        lines.append("Relations: " + '; '.join(rel_lines))
    if lessons:
        lines.append("LESSONS:")
        lines.extend(lessons)
    text = '\n'.join(lines)
    return text, tokens_estimate(text)


def retrieve(project_path, proj_folder, query, budget_tokens=600):
    """Main entry: {'text', 'tokens', 'items', 'empty'}."""
    mem = memory.load_memory(project_path, proj_folder)
    if not mem.get('entities') or not (query or '').strip():
        return {'text': '', 'tokens': 0, 'items': [], 'empty': True}
    index = build_index(mem)
    seeds = score_entities(mem, index, query)[:24]
    if not seeds:
        return {'text': '', 'tokens': 0, 'items': [], 'empty': True}
    scored = list(seeds)
    scored += expand_relations(mem, index, seeds[:8], hops=1)
    # second hop only when the budget is comfortably larger than the seed set
    seed_cost = sum(tokens_estimate(e.get('summary', '')) for _s, e in seeds)
    if seed_cost < budget_tokens * 0.5:
        scored += expand_relations(mem, index, seeds[:4], hops=2)
    # dedup keep-max
    best = {}
    for s, e in scored:
        k = e.get('name')
        if s > best.get(k, (0, None))[0]:
            best[k] = (s, e)
    ranked = sorted(best.values(), key=lambda x: (-x[0], x[1].get('name', '')))
    text, toks = render_context(ranked, mem, budget_tokens)
    # reinforcement: bump hits on injected entities (kept during consolidation)
    # and last_used on injected lessons (decay signal) — best-effort
    injected = {e.get('name') for _s, e in ranked}
    touched = False
    if text:
        for e in mem.get('entities', []):
            if e.get('name') in injected:
                e['hits'] = e.get('hits', 0) + 1
                if e.get('type') == 'lesson':
                    e['last_used'] = mem.get('session_counter', 0)
                touched = True
    if touched:
        try:
            memory.save_memory(project_path, proj_folder, mem)
        except Exception:
            pass
    return {'text': text, 'tokens': toks,
            'items': [e.get('name') for _s, e in ranked], 'empty': not text}


# ── surface estimation (launch UI) ───────────────────────────

def estimate_surfaces(project_path, proj_folder, settings):
    """What memory costs per session: digest (always), hook budget, lazy rules."""
    mem = memory.load_memory(project_path, proj_folder)
    digest = memory.build_digest_micro(mem) if mem.get('entities') else ''
    rules = []
    rules_dir = os.path.join(project_path or '', '.claude', 'rules')
    try:
        for nm in sorted(os.listdir(rules_dir)):
            if nm.startswith('claudectl-mem-'):
                p = os.path.join(rules_dir, nm)
                rules.append((nm, tokens_estimate(open(p, encoding='utf-8',
                                                       errors='ignore').read())))
    except OSError:
        pass
    hook_on = bool(settings.get('memory_prompt_hook'))
    return {'digest_tokens': tokens_estimate(digest) if digest else 0,
            'hook_budget': settings.get('memory_budget', 600) if hook_on else None,
            'rules': rules,
            'total_always': tokens_estimate(digest) if digest else 0}


def preview_screen(project_path, proj_folder, project_name):
    """What exactly gets injected, surface by surface, with token counts —
    plus a live 'type a prompt → see what the hook would inject' probe.
    TUI-only entry point (ui imports are local by design)."""
    from .ui import pager, text_input
    from .config import load_settings
    s = load_settings()
    est = estimate_surfaces(project_path, proj_folder, s)
    mem = memory.load_memory(project_path, proj_folder)
    digest = memory.build_digest_micro(mem) if mem.get('entities') else '(no memory yet)'

    lines = [f"ALWAYS LOADED — CLAUDE.md memory block  (~{est['digest_tokens']} tok)", '']
    lines += ['  ' + l for l in digest.splitlines()]
    lines += ['', f"LAZY — path-scoped rules ({len(est['rules'])} files, load only when touched)"]
    for nm, tk in est['rules']:
        lines.append(f"  {nm}  (~{tk} tok)")
    if not est['rules']:
        lines.append('  (none generated yet)')
    hook = est['hook_budget']
    lines += ['', "PER PROMPT — recall hook " +
              (f"(ON, budget {hook} tok)" if hook is not None else "(off)")]
    while True:
        key = pager(('CLAUDECTL', project_name, 'MEMORY PREVIEW'), lines,
                    hint='t try a prompt   ESC back', extra_keys=('t',))
        if key != 't':
            return
        _try_prompt(project_path, proj_folder, project_name, s)


def _try_prompt(project_path, proj_folder, project_name, settings):
    from .ui import pager, text_input
    q = text_input("Prompt to test:")
    if not q:
        return
    r = retrieve(project_path, proj_folder, q, settings.get('memory_budget', 600))
    body = r['text'].splitlines() if not r['empty'] else ['(nothing relevant — no injection)']
    pager(('CLAUDECTL', project_name, f'HOOK WOULD INJECT (~{r["tokens"]} tok)'),
          body, hint='ESC back')


def memory_status_line(project_path, proj_folder, settings):
    """One-line summary for the launch options screen."""
    try:
        est = estimate_surfaces(project_path, proj_folder, settings)
    except Exception:
        return ''
    if not est['digest_tokens'] and not est['rules'] and est['hook_budget'] is None:
        return ''
    parts = [f"~{est['digest_tokens']} tok always"]
    if est['hook_budget'] is not None:
        parts.append(f"hook <={est['hook_budget']}/prompt")
    if est['rules']:
        parts.append(f"{len(est['rules'])} rules lazy")
    return "memory: " + ' · '.join(parts)
