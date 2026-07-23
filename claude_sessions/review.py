"""Code review — review the working diff (or a branch) against the project's
CLAUDE.md rules and learned memory lessons, and report confidence-scored
findings.

Inspired by the anthropics/claude-code `code-review` plugin: confidence
scoring (0-100), a high-confidence threshold to kill false positives, and
CLAUDE.md-compliance focus. Runs a single headless `claude -p` call via
memory._claude_stdin — defaults to a strong model (review_model → exec_model),
not the economy model, because review quality matters.
"""

import json
import os
import re
import subprocess

from . import memory
from . import config as _c

SEV_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
SEV_COLOR = {'critical': _c.C_ERR, 'high': _c.C_ERR,
             'medium': _c.C_WARN, 'low': _c.C_DIM}
MAX_DIFF_CHARS = 60000


# ── git ──────────────────────────────────────────────────────

def _git(cwd, args, timeout=15):
    try:
        r = subprocess.run(['git', *args], cwd=cwd, capture_output=True,
                           text=True, encoding='utf-8', errors='ignore',
                           timeout=timeout)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''


def get_diff(project_path, staged=False, base=None):
    """The changes to review. base → `git diff base...HEAD`; staged → index vs
    HEAD; default → working tree vs HEAD (staged + unstaged). Truncated."""
    if base:
        d = _git(project_path, ['diff', f'{base}...HEAD'])
    elif staged:
        d = _git(project_path, ['diff', '--staged'])
    else:
        d = _git(project_path, ['diff', 'HEAD'])
        if not d.strip():                       # no commits yet → all changes
            d = _git(project_path, ['diff'])
    return d[:MAX_DIFF_CHARS]


# ── guidance (CLAUDE.md rules + learned lessons) ─────────────

def gather_guidance(project_path, proj_folder=None):
    parts = []
    cmd = os.path.join(project_path, 'CLAUDE.md')
    if os.path.isfile(cmd):
        try:
            with open(cmd, encoding='utf-8', errors='ignore') as f:
                txt = f.read()
            parts.append("# PROJECT RULES (from CLAUDE.md — enforce these)\n"
                         + txt[:8000])
        except Exception:
            pass
    try:
        mem = memory.load_memory(project_path, proj_folder)
        lessons = [e for e in mem.get('entities', [])
                   if e.get('type') == 'lesson'
                   and e.get('status') in ('approved', 'pinned')]
        if lessons:
            parts.append("# LEARNED LESSONS (project conventions)\n" + '\n'.join(
                f"- {e.get('name', '')}: {e.get('summary', '')}"
                for e in lessons[:20]))
    except Exception:
        pass
    return '\n\n'.join(parts)


# ── prompt + parse ───────────────────────────────────────────

def build_prompt(diff, guidance):
    return (
        "You are a meticulous senior code reviewer. Review the git diff below.\n\n"
        + (guidance + "\n\n" if guidance else "")
        + "DIFF:\n" + diff + "\n\n"
        "Report ONLY issues you are genuinely confident are real problems in the "
        "CHANGED (added/modified) lines: bugs, correctness errors, security "
        "vulnerabilities, and violations of the project rules above. Do NOT report "
        "style nits, pre-existing issues outside this diff, or speculative "
        "concerns. Distinguish rule-violations from bugs via the category.\n\n"
        "Output a JSON array and NOTHING else. Each element:\n"
        '{"file":"<path>","line":<int>,"severity":"critical|high|medium|low",'
        '"category":"bug|security|rule-violation|correctness|other",'
        '"confidence":<0-100 integer>,"summary":"<one line>",'
        '"detail":"<why it is wrong and how to fix it>"}\n'
        "If there are no real issues, output exactly []."
    )


def parse_findings(text):
    """Extract a JSON array of findings from model output. Tolerates code
    fences and a {"findings":[...]} wrapper. Returns a list (possibly empty)."""
    if not text:
        return []
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
    if m:
        t = m.group(1).strip()
    # prefer the first [...] block; fall back to a {...} wrapper
    data = None
    if '[' in t and ']' in t:
        try:
            data = json.loads(t[t.index('['):t.rindex(']') + 1])
        except Exception:
            data = None
    if data is None and '{' in t:
        obj = memory._parse_json(t)
        if isinstance(obj, dict):
            data = obj.get('findings') or obj.get('issues')
    if not isinstance(data, list):
        return []
    return [f for f in data if isinstance(f, dict)]


def _sev_key(f):
    return (SEV_ORDER.get(str(f.get('severity', '')).lower(), 4),
            -int(f.get('confidence', 0) or 0))


# ── run ──────────────────────────────────────────────────────

def run_review(project_path, proj_folder=None, staged=False, base=None,
               min_confidence=None, model=None, silent=False):
    """Return {'findings': [...], 'empty': bool, 'raw_count': int, 'min': int}.
    'empty' means there was no diff to review."""
    from .config import load_settings
    s = load_settings()
    if min_confidence is None:
        min_confidence = int(s.get('review_min_confidence', 80) or 0)
    model = model or s.get('review_model') or s.get('exec_model') or ''

    diff = get_diff(project_path, staged=staged, base=base)
    if not diff.strip():
        return {'findings': [], 'empty': True, 'raw_count': 0, 'min': min_confidence}

    guidance = gather_guidance(project_path, proj_folder)
    prompt = build_prompt(diff, guidance)
    if silent:
        memory._tls.silent = True
    try:
        out = memory._claude_stdin(prompt, cwd=project_path, timeout=300,
                                   crumbs=('CLAUDECTL', 'REVIEW'),
                                   label='Reviewing changes with Claude...',
                                   model=model)
    finally:
        if silent:
            memory._tls.silent = False
    raw = parse_findings(out)
    kept = [f for f in raw if int(f.get('confidence', 0) or 0) >= min_confidence]
    kept.sort(key=_sev_key)
    return {'findings': kept, 'empty': False, 'raw_count': len(raw),
            'min': min_confidence}


# ── render ───────────────────────────────────────────────────

def render_lines(result):
    """Colored text lines summarizing a review result (for pager / CLI)."""
    if result.get('empty'):
        return [f"{_c.C_DIM}No changes to review (empty diff).{_c.C_RESET}"]
    findings = result['findings']
    if not findings:
        n = result.get('raw_count', 0)
        tail = (f"  {_c.C_DIM}({n} lower-confidence note(s) filtered out below "
                f"{result['min']}%){_c.C_RESET}" if n else '')
        return [f"{_c.C_OK}No issues found above the confidence threshold.{_c.C_RESET}" + tail]
    lines = [f"{_c.C_BOLD}Found {len(findings)} issue(s):{_c.C_RESET}", '']
    for f in findings:
        sev = str(f.get('severity', '?')).lower()
        col = SEV_COLOR.get(sev, _c.C_RESET)
        loc = f"{f.get('file', '?')}:{f.get('line', '?')}"
        lines.append(f"{col}● {sev.upper():<8}{_c.C_RESET} {_c.C_DIM}[{f.get('category', '')}, "
                     f"{f.get('confidence', '?')}%]{_c.C_RESET}  {loc}")
        lines.append(f"    {f.get('summary', '')}")
        detail = (f.get('detail') or '').strip()
        if detail:
            lines.append(f"    {_c.C_DIM}{detail}{_c.C_RESET}")
        lines.append('')
    return lines


# ── TUI screen ───────────────────────────────────────────────

def review_screen(project_path, proj_folder, project_name):
    """Run a review (visible progress) and page the findings."""
    from .ui import pager, flash
    if not _git(project_path, ['rev-parse', '--is-inside-work-tree']).strip():
        flash("Not a git repository — nothing to review", ok=False, secs=1.8)
        return
    result = run_review(project_path, proj_folder)   # foreground: shows progress
    lines = render_lines(result)
    pager(('CLAUDECTL', project_name, 'REVIEW'), lines,
          hint='confidence-scored review of your working changes')


# ── CLI: `claudectl review [--staged|--branch BASE] [--min-confidence N] [path]` ──

def review_cli(argv):
    staged = '--staged' in argv
    base = None
    min_conf = None
    path = None
    it = iter(argv)
    for a in it:
        if a == '--branch':
            base = next(it, None)
        elif a == '--min-confidence':
            try:
                min_conf = int(next(it, '80'))
            except Exception:
                min_conf = None
        elif a in ('--staged',):
            pass
        elif not a.startswith('--'):
            path = a
    project_path = os.path.abspath(path or os.getcwd())
    if not os.path.isdir(os.path.join(project_path, '.git')) and \
       not _git(project_path, ['rev-parse', '--is-inside-work-tree']).strip():
        print(f"  Not a git repository: {project_path}")
        return 1
    print(f"  Reviewing {project_path} …")
    result = run_review(project_path, None, staged=staged, base=base,
                        min_confidence=min_conf, silent=True)
    from . import render
    for ln in render_lines(result):
        print('  ' + render.strip_ansi(ln) if not _supports_color() else '  ' + ln)
    return 0


def _supports_color():
    return bool(sys.stdout.isatty())
