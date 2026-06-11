import os
import subprocess
import time

from .config import W
from .ui import text_input, menu, _cls, pause


def ai_generate_system_prompt(sp_path, project_name, project_path, proj_folder):
    """Use Claude --print to generate a system prompt for this project."""
    claude_exe = os.path.join(os.environ['USERPROFILE'], '.local', 'bin', 'claude.exe')

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

    _cls()
    print(f"\n  AI SYSTEM PROMPT  /  {project_name}  — generating...\n", flush=True)

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
        f"Generate a system-prompt.txt file for a Claude Code project named '{project_name}'.\n\n"
        f"{context_block}"
        f"{existing_block}"
        f"{extra_block}"
        f"The system prompt is injected before every Claude session in this project via --system-prompt-file.\n"
        f"Write it as plain text (no markdown code fences). Include:\n"
        f"- Role/persona for Claude (what kind of engineer, what platform)\n"
        f"- Key codebase rules and conventions specific to this project\n"
        f"- Behavior guidelines (how to respond, what to avoid)\n"
        f"- Any language/tone rules\n\n"
        f"Output ONLY the system prompt text. No preamble, no explanation, no code fences."
    )

    try:
        r = subprocess.run([claude_exe, '--print', prompt],
                           capture_output=True, text=True, timeout=60)
        content = r.stdout.strip()
        if content:
            with open(sp_path, 'w', encoding='utf-8') as f:
                f.write(content)
            _cls()
            print(f"\n  ✔ System prompt generated for {project_name}\n")
            print(f"  Opening in Notepad++ to review...\n")
            time.sleep(1)
            subprocess.Popen([r'C:\Program Files\Notepad++\notepad++.exe', sp_path])
        else:
            _cls()
            print(f"\n  ✘ No output from Claude.\n")
            pause("  Press Enter...")
    except Exception as e:
        _cls()
        print(f"\n  ✘ Error: {e}\n")
        pause("  Press Enter...")


def edit_system_prompt(proj_folder, project_name, project_path=None):
    sp_path = os.path.join(proj_folder, 'system-prompt.txt')

    # Ask: AI generate or manual edit
    exists = os.path.exists(sp_path)
    action_items = [
        ('✦  Generate with AI' + (' (update existing)' if exists else ' (fresh)'), 'ai'),
        ('📝  Edit manually in Notepad++', 'manual'),
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
    try:
        subprocess.Popen([r'C:\Program Files\Notepad++\notepad++.exe', sp_path])
    except Exception:
        pass
