"""Plan→Execute — two-model run from the launcher.

Plan a task headlessly with an accurate model (default Opus 4.8), let the user
approve/edit the plan, then launch an interactive session with a cheaper/faster
model (default Sonnet 5) seeded with the approved plan. Big token savings:
expensive reasoning happens once, execution runs on the cheap tier.

The plan is written to <project>/.claudectl/plan-latest.md and the execution
session gets a SHORT `--append-system-prompt` pointer (avoids the Windows argv
length limit — the model reads the full plan from disk with its own tools).
"""

import os
import subprocess

from . import config as _c

PLAN_FILE = os.path.join('.claudectl', 'plan-latest.md')


def _plan(task, plan_model, cwd):
    """Headless plan generation with the plan model. Returns plan text or ''."""
    from .config import get_claude_exe
    from .ui import run_with_progress_stdin
    exe = get_claude_exe()
    if not exe:
        return ''
    prompt = (
        "Produce a concise, actionable implementation PLAN for the task below. "
        "Number the steps; name the files/functions to touch; note key edge cases "
        "and how to verify. Do NOT write code or use any tools — output only the "
        "plan as markdown.\n\nTASK:\n" + task
    )
    args = [exe, '-p', '--model', plan_model,
            '--disallowedTools', 'Write,Edit,NotebookEdit,Bash']
    out, _cancelled = run_with_progress_stdin(
        args, prompt, ('CLAUDECTL', 'PLAN'),
        f'Planning with {plan_model}...', timeout=600, cwd=cwd)
    return (out or '').strip()


def run(project_path, proj_folder, project_name):
    """Interactive Plan→Execute flow. Returns True if an execution session was
    launched, False if cancelled."""
    from .ui import text_input, pager, flash, _cls
    from .config import load_settings, get_claude_exe, config_dir
    from . import diffview
    from .sessions import load_add_dirs, read_extra_paths

    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = config_dir
    extra = read_extra_paths(proj_folder)
    if extra:
        env['PATH'] = ';'.join(extra) + ';' + env.get('PATH', '')

    s = load_settings()
    plan_model = s.get('plan_model', 'claude-opus-4-8')
    exec_model = s.get('exec_model', 'claude-sonnet-5')

    task = text_input(f"Task to plan ({plan_model}) then execute ({exec_model}):")
    if not task:
        return False

    plan = _plan(task, plan_model, project_path)
    if not plan:
        flash("Planning failed or cancelled", ok=False, secs=1.8)
        return False

    # review / approve (f in the pager toggles nothing here — simple approve/reject)
    if not diffview.confirm('', plan, f"PLAN ({plan_model}) — approve to execute"):
        _cls()
        print("\n  Plan rejected — nothing launched.\n")
        import time
        time.sleep(1)
        return False

    plan_path = os.path.join(project_path, PLAN_FILE)
    try:
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(f"# Plan: {task}\n\n{plan}\n")
    except Exception as e:
        flash(f"Could not save plan: {e}", ok=False, secs=2)
        return False

    exe = get_claude_exe()
    if not exe:
        flash("claude.exe not found", ok=False, secs=1.8)
        return False

    # short pointer keeps --append-system-prompt well under the Windows argv cap;
    # the model reads the full plan from disk with its own tools
    pointer = (f"An approved implementation plan for this task is saved at "
               f"{PLAN_FILE.replace(os.sep, '/')}. Read it first, then execute it "
               f"step by step. Task: {task[:200]}")
    args = [exe, '--model', exec_model, '--append-system-prompt', pointer]
    sp_file = os.path.join(proj_folder, 'system-prompt.txt') if proj_folder else ''
    if sp_file and os.path.isfile(sp_file):
        args += ['--system-prompt-file', sp_file]
    add_dirs = [d for d in load_add_dirs(proj_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]

    _cls()
    print(f"  Plan saved: {plan_path}")
    print(f"  Executing with {exec_model} (planned by {plan_model})")
    print(f"  {'-' * 42}\n")
    try:
        subprocess.call(args, cwd=project_path, env=env)
    except Exception as e:
        print(f"\n  Execution launch failed: {e}")
        import time
        time.sleep(2)
    return True
