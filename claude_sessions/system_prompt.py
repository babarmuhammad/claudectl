import os
import subprocess
import time

from .config import W, get_claude_exe, open_in_editor, find_editor
from .ui import text_input, menu, _cls, pause, run_with_progress, flash


def merged_system_prompt(sp_file, pointer, out_path):
    """Merge the project's existing system prompt (if any) with a pointer line
    and write it to out_path as ONE file. Used instead of stacking
    --append-system-prompt on top of --system-prompt-file — when a project
    already has a system-prompt.txt, --system-prompt-file silently replaces
    the whole system prompt and the appended pointer never reaches Claude."""
    base = ''
    if sp_file and os.path.isfile(sp_file):
        try:
            base = open(sp_file, encoding='utf-8', errors='ignore').read().strip()
        except Exception:
            pass
    text = (base + '\n\n' if base else '') + pointer
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return out_path


def ai_generate_system_prompt(sp_path, project_name, project_path, proj_folder):
    """Use Claude --print to generate a system prompt for this project."""
    claude_exe = get_claude_exe()
    if not claude_exe:
        _cls()
        print(f"\n  ✘ claude.exe not found — cannot generate.\n")
        pause("  Press Enter...")
        return

    # Read existing CLAUDE.md for context
    md_path = os.path.join(project_path, 'CLAUDE.md')
    claude_md = ''
    if os.path.exists(md_path):
        try:
            claude_md = open(md_path, encoding='utf-8', errors='ignore').read()[:3000]
        except Exception:
            pass

    # Optional extra instructions
    _cls()
    print(f"\n  AI SYSTEM PROMPT  /  {project_name}\n")
    print(f"  Optional: extra instructions for generation (ENTER to skip)\n")
    print(f"  Example: 'always respond in Italian' / 'focus on build system rules'\n")
    extra = text_input("Extra instructions:", default='') or ''

    existing = ''
    if os.path.exists(sp_path):
        try:
            existing = open(sp_path, encoding='utf-8', errors='ignore').read().strip()
        except Exception:
            pass

    context_block = f"CLAUDE.MD CONTENT:\n{claude_md}\n\n" if claude_md else ''
    existing_block = f"EXISTING SYSTEM PROMPT (update it, preserve good parts):\n{existing}\n\n" if existing else ''
    extra_block = f"ADDITIONAL INSTRUCTIONS: {extra}\n\n" if extra else ''

    prompt = (
        f"Compose the text of a system prompt for a Claude Code project named '{project_name}'.\n\n"
        f"{context_block}"
        f"{existing_block}"
        f"{extra_block}"
        f"The system prompt is injected before every Claude session in this project via --system-prompt-file.\n"
        f"Plain text (no markdown code fences). Include:\n"
        f"- Role/persona for Claude (what kind of engineer, what platform)\n"
        f"- Key codebase rules and conventions specific to this project\n"
        f"- Behavior guidelines (how to respond, what to avoid)\n"
        f"- Any language/tone rules\n\n"
        f"Do NOT create, write, or edit any files and do not use any tools — "
        f"return the system prompt text directly as your response.\n"
        f"Output ONLY the system prompt text. No preamble, no explanation, no code fences."
    )

    # prompt BEFORE --disallowedTools: the flag is variadic and would
    # otherwise swallow the prompt as tool names
    from .memory import extract_model
    _mf = ['--model', extract_model()] if extract_model() else []
    out, cancelled = run_with_progress(
        [claude_exe, *_mf, '--print', prompt,
         '--disallowedTools', 'Write,Edit,NotebookEdit,Bash'],
        ('CLAUDECTL', project_name, 'AI SYSTEM PROMPT'),
        'Generating system prompt with Claude...  (15-60s)',
        timeout=120)
    if cancelled:
        flash("Generation cancelled", ok=False)
        return
    content = (out or '').strip()
    if content:
        # Preview the DIFF (old → proposed) before writing — approve or reject.
        from . import diffview
        if not diffview.confirm(existing, content, f"SYSTEM PROMPT  /  {project_name}"):
            _cls()
            print(f"\n  Rejected — system prompt not written.\n")
            time.sleep(1)
            return
        try:
            with open(sp_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            _cls()
            print(f"\n  ✘ Error writing file: {e}\n")
            pause("  Press Enter...")
            return
        try:
            diffview.record(project_path, proj_folder, 'system_prompt', existing, content)
        except Exception:
            pass
        _cls()
        print(f"\n  ✔ System prompt generated for {project_name}\n")
        print(f"  Opening in editor to review...\n")
        time.sleep(1)
        open_in_editor(sp_path)
    else:
        _cls()
        print(f"\n  ✘ No output from Claude (timeout or empty response).\n")
        pause("  Press Enter...")


def edit_system_prompt(proj_folder, project_name, project_path=None):
    sp_path = os.path.join(proj_folder, 'system-prompt.txt')

    # Ask: AI generate or manual edit
    exists = os.path.exists(sp_path)
    action_items = [
        ('✦  Generate with AI' + (' (update existing)' if exists else ' (fresh)'), 'ai'),
        ('📝  Edit manually in editor', 'manual'),
    ]
    sel = menu(action_items, f"SYSTEM PROMPT  /  {project_name}")
    if not sel:
        return

    if sel == 'ai':
        if not project_path:
            project_path = ''
        ai_generate_system_prompt(sp_path, project_name, project_path, proj_folder)
        return

    # manual
    if not exists:
        try:
            with open(sp_path, 'w', encoding='utf-8') as f:
                f.write(f"# System prompt — {project_name}\n"
                        f"# Passed via --system-prompt-file on every launch for this project.\n\n")
        except Exception:
            return
    if not open_in_editor(sp_path):
        _cls()
        print(f"\n  ✘ No editor found. Edit manually: {sp_path}\n")
        pause("  Press Enter...")
