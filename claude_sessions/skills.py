"""Skill management — browse, scaffold, AI-generate, edit, delete, and install
Claude Code *skills* (`.claude/skills/<name>/SKILL.md`).

A skill is a directory holding a `SKILL.md` (YAML frontmatter: name,
description, optional allowed-tools) plus optional supporting files. Claude
Code discovers skills in a project's `.claude/skills/` and loads each one's
body **on demand** (progressive disclosure) — cheaper context than an
always-on CLAUDE.md.

Three scopes:
  - **templates**  — starter skills bundled in the package (skills_templates/),
                     read-only, each credited to its upstream source.
  - **library**    — the user's own skills (config.skills_library_dir).
  - **project**    — <project>/.claude/skills/, what Claude actually reads.

Mirrors agents.py so the two managers feel identical.
"""

import os
import re
import shutil

from .config import W, get_claude_exe, open_in_editor, MODELS, MODEL_LABELS
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


def library_dir():
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
            for line in fm.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip()
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
    """Bundled templates + user-library skills, deduped by folder name
    (library wins). [(name, description, skill_dir, source)]."""
    seen = {}
    out = []
    for name, desc, d in list_skills(bundled_templates_dir()):
        seen[os.path.basename(d)] = len(out)
        out.append((name, desc, d, 'template'))
    for name, desc, d in list_skills(library_dir()):
        key = os.path.basename(d)
        if key in seen:
            out[seen[key]] = (name, desc, d, 'library')
        else:
            out.append((name, desc, d, 'library'))
    return out


def _slug(name):
    return re.sub(r'[^a-z0-9-]+', '-', (name or '').lower()).strip('-') or 'skill'


# ── install / remove ─────────────────────────────────────────

def install_skill(src_dir, project_path):
    """Copy a skill folder into <project>/.claude/skills/. Returns dest dir or ''."""
    if not src_dir or not project_path or not os.path.isfile(skill_md(src_dir)):
        return ''
    dest = os.path.join(project_skills_dir(project_path), os.path.basename(src_dir))
    try:
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


def install_from_git(repo_url, project_path, exec_model=''):
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
        try:
            r = subprocess.run(['git', 'clone', '--depth', '1', repo_url, tmp],
                               capture_output=True, text=True, timeout=60)
        except Exception as e:
            return False, f'git not available: {e}'
        if r.returncode != 0:
            return False, f'git clone failed: {(r.stderr or "").strip()[:200]}'

        installed = []
        skills_src = os.path.join(tmp, 'skills')
        if os.path.isdir(skills_src):
            for name in os.listdir(skills_src):
                d = os.path.join(skills_src, name)
                if os.path.isfile(skill_md(d)):
                    dest = install_skill(d, project_path) if project_path else save_to_library(d)
                    if dest:
                        installed.append(name)

        agent_count = 0
        agents_src = os.path.join(tmp, 'agents')
        if os.path.isdir(agents_src):
            agents_dest = os.path.join(_c.config_dir, 'agents')
            os.makedirs(agents_dest, exist_ok=True)
            for fn in os.listdir(agents_src):
                if not fn.endswith('.md'):
                    continue
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


def save_to_library(src_dir):
    """Copy a project/template skill into the user library. Returns dest or ''."""
    if not os.path.isfile(skill_md(src_dir)):
        return ''
    dest = os.path.join(library_dir(), os.path.basename(src_dir))
    try:
        os.makedirs(library_dir(), exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest)
        return dest
    except Exception:
        return ''


# ── TUI menu ─────────────────────────────────────────────────

def skills_menu(project_path=None):
    """Browse templates + user library; create/AI-generate/edit/delete/install
    skills. When project_path is given, project skills and 'install' appear."""
    while True:
        items = []
        tmpls = list_templates()
        if tmpls:
            items.append((f"{_c.C_DIM}── templates & library ──{_c.C_RESET}", None))
            for name, desc, d, src in tmpls:
                tag = f"  {_c.C_DIM}[{src}]{_c.C_RESET}"
                tail = f"  {_c.C_DIM}{render.trunc(desc, 40)}{_c.C_RESET}" if desc else ''
                items.append((f"{name}{tag}{tail}", f'tmpl:{d}'))
        if project_path:
            proj = list_skills(project_skills_dir(project_path))
            items.append((f"{_c.C_DIM}── this project ({len(proj)}) ──{_c.C_RESET}", None))
            for name, desc, d in proj:
                tail = f"  {_c.C_DIM}{render.trunc(desc, 40)}{_c.C_RESET}" if desc else ''
                items.append((f"✓ {name}{tail}", f'proj:{d}'))
        items += [(f"{'─' * W}", None),
                  ('＋  New skill (manual)', '__new__'),
                  ('✦  New skill (AI-generated)', '__ai__'),
                  ('🌐  Install skill+agents from GitHub…', '__git__')]

        sel = menu(items, "SKILLS  /  " + (os.path.basename(project_path) if project_path
                                           else 'library'))
        if not sel:
            return
        if sel == '__new__':
            _new_skill_manual(project_path)
        elif sel == '__ai__':
            _new_skill_ai(project_path)
        elif sel == '__git__':
            _new_skill_from_git(project_path)
        elif sel.startswith('tmpl:'):
            _template_detail(sel[5:], project_path)
        elif sel.startswith('proj:'):
            _project_skill_detail(sel[5:])


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


def _template_detail(skill_dir, project_path):
    meta, _ = parse_skill(skill_dir)
    name = meta.get('name', os.path.basename(skill_dir))
    items = [
        (f"Name        :  {name}", None),
        (f"Description :  {render.trunc(meta.get('description', ''), W - 18)}", None),
        (f"{'─' * W}", None),
        ('👁  View SKILL.md', 'view'),
    ]
    if project_path:
        items.append(('⬇  Install into this project', 'install'))
    items.append(('📚  Copy to my library', 'lib'))
    sel = menu(items, f"SKILL  /  {name}")
    if sel == 'view':
        view_skill_file(skill_dir)
    elif sel == 'install':
        dest = install_skill(skill_dir, project_path)
        flash(f"Installed → {os.path.basename(dest)}" if dest
              else "Install failed", ok=bool(dest), secs=1.4)
    elif sel == 'lib':
        dest = save_to_library(skill_dir)
        flash("Saved to library" if dest else "Copy failed", ok=bool(dest), secs=1.4)


def _project_skill_detail(skill_dir):
    meta, _ = parse_skill(skill_dir)
    name = meta.get('name', os.path.basename(skill_dir))
    sel = menu([
        (f"Name        :  {name}", None),
        (f"Description :  {render.trunc(meta.get('description', ''), W - 18)}", None),
        (f"{'─' * W}", None),
        ('👁  View SKILL.md', 'view'),
        ('📝  Edit in editor', 'edit'),
        ('📚  Copy to my library', 'lib'),
        ('🗑  Remove from project', 'delete'),
    ], f"SKILL  /  {name}")
    if sel == 'view':
        view_skill_file(skill_dir)
    elif sel == 'edit':
        open_in_editor(skill_md(skill_dir))
    elif sel == 'lib':
        dest = save_to_library(skill_dir)
        flash("Saved to library" if dest else "Copy failed", ok=bool(dest), secs=1.4)
    elif sel == 'delete':
        if confirm(f"Remove skill '{name}' from project?", danger=True):
            flash("Removed" if delete_skill(skill_dir) else "Remove failed",
                  ok=True, secs=1.2)


def _dest_dir(project_path):
    """Where a newly-created skill lands: project if we have one, else library."""
    return project_skills_dir(project_path) if project_path else library_dir()


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


def write_skill_raw(project_path, name, content):
    """Write approved AI-generated markdown as <dest>/<slug>/SKILL.md. Returns
    the skill dir on success, '' on failure. Used by the GUI job."""
    skill_dir = os.path.join(_dest_dir(project_path), _slug(name))
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
        flash("No output from Claude", ok=False, secs=1.4); return
    if not _pager_confirm(f"SKILL  /  {_slug(name)}  — approve to write", content):
        _cls(); print("\n  Rejected — not written.\n"); pause("  Press Enter..."); return
    if write_skill_raw(project_path, name, content):
        flash(f"Created {_slug(name)}/SKILL.md")
        open_in_editor(skill_md(os.path.join(_dest_dir(project_path), _slug(name))))
    else:
        flash("Write failed", ok=False, secs=1.6)
