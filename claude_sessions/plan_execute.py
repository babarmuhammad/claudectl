"""Plan→Execute — two-model run from the launcher.

Plan a task headlessly with an accurate model (default Opus 4.8), let the user
approve/edit the plan, then launch an interactive session with a cheaper/faster
model (default Sonnet 5) seeded with the approved plan. Big token savings:
expensive reasoning happens once, execution runs on the cheap tier.

The plan is written to <project>/.claudectl/plan-latest.md and the execution
session gets a SHORT `--append-system-prompt` pointer (avoids the Windows argv
length limit — the model reads the full plan from disk with its own tools).
"""

import json
import os
import re
import subprocess

from . import config as _c

PLAN_FILE = os.path.join('.claudectl', 'plan-latest.md')


def plan_store_path():
    """Return path for plan persistence (JSON), stored near the settings file."""
    return os.path.join(os.path.expanduser('~'), '.claude', 'last_plan.json')


def save_plan(plan_text, path):
    """Write plan text and metadata to path as JSON."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'plan_text': plan_text, 'generated_at': None}, f, indent=2)


def load_plan(path):
    """Read plan text from JSON path, returns plan_text string or ''."""
    try:
        d = json.loads(open(path, 'r', encoding='utf-8').read())
        return d.get('plan_text', '')
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return ''


# The plan is written once by a strong/expensive model but EXECUTED by a
# cheaper one (that's the whole point of Plan->Execute) -- so the plan itself
# has to compensate for the executor's weaker reasoning: no step it has to
# fill in the blanks on. Shared by _plan() and the council synthesis step so
# a council-optimized plan can't drop this and regress to vague steps.
WEAK_MODEL_PLAN_INSTRUCTIONS = (
    "The plan will be carried out by a LESS capable model than you -- it cannot "
    "fill in gaps or use judgment on ambiguity. Write every step so it can:\n"
    "- Numbered, one atomic action per step (one file/function edit or one "
    "command per step, not a bundle of edits).\n"
    "- Exact file paths and function/symbol names per step, never \"the "
    "relevant file\" or \"the appropriate function\".\n"
    "- Say exactly what to change (old -> new), never \"update as needed\", "
    "\"figure out\", or \"handle appropriately\".\n"
    "- State preconditions and edge cases inline in the step they affect, "
    "not as a separate general note.\n"
    "- End each step with a concrete verify command/check and the expected "
    "result.\n"
    "- Each step self-contained -- readable and actionable without needing "
    "context from other steps."
)


# ── edit plan (pure) ───────────────────────────────────────────

def _step_lines(plan):
    """Find all step lines (matching ``^\\d+\\. ``). Returns (indices, lines)
    where indices are positions in the original splitlines and lines hold the
    matched text."""
    indices, lines = [], []
    for i, ln in enumerate(plan.splitlines()):
        if re.match(r'^\d+\.\s', ln):
            indices.append(i)
            lines.append(ln)
    return indices, lines


def edit_plan(plan, action, index=None, text=''):
    """Return modified plan string. Pure function — no side effects.

    Actions:
      'edit'   — replace step at *index* with *text*
      'delete' — drop step at *index*
      'insert' — add *text* as new step at *index* (before existing step)
      'move'   — move step at *index* to *text* position (``int(text)``)

    Returns the renumbered plan, or raises ValueError on invalid index/empty
    text."""
    lines = plan.splitlines()
    si, steps = _step_lines(plan)
    n = len(steps)
    if not si:
        raise ValueError('No numbered steps found in plan')
    if index is not None and (index < 0 or index > n or (action != 'insert' and index >= n)):
        raise ValueError(f'Step index {index} out of range (0-{n - 1})')

    if action == 'edit':
        if not text.strip():
            raise ValueError('Step text cannot be empty')
        lines[si[index]] = re.sub(r'^\d+\.\s', '', lines[si[index]])
        lines[si[index]] = f'{index + 1}. {text}'

    elif action == 'delete':
        del lines[si[index]]
        si = [i if i < si[index] else i - 1 for i in si]
        si.pop(index)

    elif action == 'insert':
        if not text.strip():
            raise ValueError('Step text cannot be empty')
        pos = si[index] if index < n else len(lines)
        lines.insert(pos, f'{index + 1}. {text}')
        si = [i if i < pos else i + 1 for i in si]

    elif action == 'move':
        dst = int(text)
        if dst < 0 or dst >= n:
            raise ValueError(f'Target position {dst} out of range (0-{n - 1})')
        if dst == index:
            return plan
        line = lines.pop(si[index])
        new_idx = si[dst]
        lines.insert(new_idx, line)
        si = [i if i < new_idx else i + 1 for i in si]

    else:
        raise ValueError(f'Unknown action {action!r}')

    # renumber steps
    step_i = 0
    for i, ln in enumerate(lines):
        if re.match(r'^\d+\.\s', ln):
            step_i += 1
            lines[i] = re.sub(r'^\d+', str(step_i), ln)
    return '\n'.join(lines)


def _plan(task, plan_model, cwd, effort=''):
    """Headless plan generation with the plan model. Returns plan text or ''.
    effort matters here more than almost anywhere else in claudectl -- this
    is the ONE call that does the expensive reasoning, so it's worth paying
    for xhigh/max if the task is hard; a cheap effort here undermines the
    entire point of Plan→Execute.

    run_with_progress_stdin itself is silent-aware (memory._tls.silent, set
    by every GUI job thread) -- no separate branch needed here; see its
    docstring for why that check exists at all (it isn't optional)."""
    from .config import get_claude_exe
    exe = get_claude_exe()
    if not exe:
        return ''
    prompt = (
        "Produce a concise, actionable implementation PLAN for the task below. "
        "Number the steps; name the files/functions to touch; note key edge cases "
        "and how to verify. Do NOT write code or use any tools — output only the "
        "plan as markdown.\n\n" + WEAK_MODEL_PLAN_INSTRUCTIONS +
        "\n\nTASK:\n" + task
    )
    args = [exe, '-p', '--model', plan_model,
            '--disallowedTools', 'Write,Edit,NotebookEdit,Bash']
    if effort:
        args += ['--effort', effort]

    from .ui import run_with_progress_stdin
    out, _cancelled = run_with_progress_stdin(
        args, prompt, ('CLAUDECTL', 'PLAN'),
        f'Planning with {plan_model}...', timeout=600, cwd=cwd)
    result = (out or '').strip()
    if result:
        try: save_plan(result, plan_store_path())
        except OSError: pass
    return result


def _headless(model, prompt, cwd, omni_env=None):
    """One-shot headless call to `model` -- same plain-subprocess pattern
    _plan() uses for the silent/background path, minus the progress bar
    (council voices run back-to-back, not worth a renderer each).

    omni_env: same ANTHROPIC_BASE_URL/AUTH_TOKEN override _plan()'s exec half
    already supports (config.omniroute_env()) -- routes council calls through
    the free-tier proxy too when it's configured, since a council is N extra
    calls per plan and that's exactly where the extra cost shows up.

    Caller picks the right roster for the target (COUNCIL_MODELS for direct
    API, OMNI_COUNCIL_MODELS for OmniRoute) -- model arrives here ready to use
    as-is. Previously this guessed an 'anthropic/' prefix for OmniRoute, but
    OmniRoute's catalog has no such namespace (providers are aggregator-named:
    'aug/', 'tllm/', 'ddgw/', ...); that guess always 404'd.

    Returns stdout text or '' on failure/missing exe."""
    from .config import get_claude_exe
    exe = get_claude_exe()
    if not exe:
        return ''
    args = [exe, '-p', '--model', model, '--disallowedTools', 'Write,Edit,NotebookEdit,Bash']
    env = os.environ.copy()
    if omni_env:
        env.update(omni_env)
    try:
        p = subprocess.run(args, input=prompt, capture_output=True, text=True,
                           encoding='utf-8', errors='ignore', cwd=cwd, timeout=600, env=env)
        return (p.stdout or '').strip()
    except Exception:
        return ''


COUNCIL_MODELS = ['claude-sonnet-5', 'claude-opus-4-8', 'claude-haiku-4-5']

# OmniRoute's own stable meta-router ids (confirmed present in its live
# /v1/models catalog) -- used instead of COUNCIL_MODELS when routing through
# OmniRoute, since raw provider namespaces there are aggregator-specific
# ('aug/', 'tllm/', 'ddgw/', ...), not 'anthropic/'. Each 'auto/*' variant
# server-side-picks and fails over across whatever's actually healthy, which
# is also a reasonable stand-in for "3 distinct critique voices".
OMNI_COUNCIL_MODELS = ['auto/best-reasoning', 'auto/best-coding', 'auto/best-fast']


def optimize_plan_council(task, plan, cwd, models=None, omni_env=None):
    """Fan the draft plan out to a small council of OTHER models for critique,
    then synthesize one improved plan. Disabled callers simply never call
    this -- zero extra token cost. Routes through OmniRoute (omni_env) when
    configured, same free-tier proxy the exec half already uses, so a
    council doesn't have to mean N extra paid Anthropic calls. Returns the
    original plan unchanged if: the plan is too short to bother, fewer than
    2 distinct models are configured, or every council voice fails to
    answer."""
    if not plan or len(plan) < 40:
        return plan
    roster, seen = [], set()
    for m in (models or (OMNI_COUNCIL_MODELS if omni_env else COUNCIL_MODELS)):
        if m and m not in seen:
            seen.add(m)
            roster.append(m)
    if len(roster) < 2:
        return plan

    critique_prompt = (
        "Below is a draft implementation PLAN for a task. Critique it: gaps, "
        "wrong assumptions, missing edge cases, better approaches. Bullet "
        "points only, no restated plan, no code.\n\n"
        f"TASK:\n{task}\n\nDRAFT PLAN:\n{plan}"
    )
    critiques = []
    for m in roster:
        out = _headless(m, critique_prompt, cwd, omni_env)
        if out:
            critiques.append((m, out))
    if not critiques:
        return plan

    synth_prompt = (
        "Merge the draft plan below with the council critiques that follow "
        "into ONE improved, concise implementation plan. Keep numbered "
        "steps, file/function names, edge cases, and a verify step. Output "
        "only the final plan as markdown, nothing else, no tools.\n\n"
        + WEAK_MODEL_PLAN_INSTRUCTIONS +
        f"\n\nTASK:\n{task}\n\nDRAFT PLAN:\n{plan}\n\n"
        + "\n\n".join(f"CRITIQUE ({m}):\n{c}" for m, c in critiques)
    )
    merged = _headless(roster[0], synth_prompt, cwd, omni_env)
    return merged or plan


def write_plan_file(project_path, task, plan):
    """Write the approved plan to <project>/.claudectl/plan-latest.md. Returns
    the absolute path, or '' on failure. Shared by the TUI and GUI flows so
    there's exactly one plan-file format."""
    plan_path = os.path.join(project_path, PLAN_FILE)
    try:
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(f"# Plan: {task}\n\n{plan}\n")
        return plan_path
    except Exception:
        return ''


def replan(task, feedback):
    """Re-generate plan with feedback appended to the original task prompt."""
    prompt = task + "\n\nFeedback/constraints based on prior review:\n" + feedback
    plan = _plan(prompt, _c.load_settings().get('plan_model', 'claude-opus-4-8'),
                 os.getcwd())
    if plan:
        save_plan(plan, plan_store_path())
    return plan


def replan_from_plan(plan_text, feedback, plan_model, cwd, effort=''):
    """Regenerate plan keeping original content + feedback."""
    revised = _plan(f"Original plan:\n{plan_text}\n\nFeedback:\n{feedback}",
                    plan_model, cwd, effort)
    if revised:
        save_plan(revised, plan_store_path())
    return revised


def build_exec_launch(project_path, proj_folder, task, exec_model, omni_env=None, cfgdir=''):
    """Assemble (args, env) for the Plan→Execute *execute* session — the ONE
    place both the TUI (run(), below) and the GUI (gui_api's `plan_launch`
    job kind) build this, so they can't drift apart.

    Sets CLAUDE_CONFIG_DIR, merges the project's own system-prompt.txt with a
    short plan-file pointer (avoids the Windows argv length cap — the model
    reads the full plan from disk with its own tools), and adds --add-dir /
    extra PATH entries. This — plus `cwd=project_path` at launch — is also
    what makes the execute session auto-discover whatever agents/skills are
    selected for this project: Claude Code reads .claude/agents/ and
    .claude/skills/ from cwd, and both are already synced there at selection
    time (agents.py/skills.py); nothing else needs to be threaded through.

    cfgdir: resolved config dir of the account to launch under ('' = current
    active account, same as every other launch path in the app).

    Returns (None, None) if claude.exe can't be found.
    """
    from .config import get_claude_exe, config_dir
    from .sessions import load_add_dirs, read_extra_paths
    from .system_prompt import merged_system_prompt

    exe = get_claude_exe()
    if not exe:
        return None, None

    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = cfgdir or config_dir
    extra = read_extra_paths(proj_folder)
    if extra:
        env['PATH'] = ';'.join(extra) + ';' + env.get('PATH', '')
    if omni_env:
        env.update(omni_env)

    pointer = (f"An approved implementation plan for this task is saved at "
               f"{PLAN_FILE.replace(os.sep, '/')}. Read it first, then execute it "
               f"step by step. Task: {task[:200]}")
    sp_file = os.path.join(proj_folder, 'system-prompt.txt') if proj_folder else ''
    merged_path = os.path.join(project_path, '.claudectl', 'plan-system-prompt.txt')
    merged_system_prompt(sp_file, pointer, merged_path)

    args = [exe, '--model', exec_model, '--system-prompt-file', merged_path]
    add_dirs = [d for d in load_add_dirs(proj_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]
    return args, env


def plan_review_loop(plan, title=''):
    """Show the plan, let the user approve/reject/edit. Returns the (possibly
    modified) plan on approve, or None on reject.

    Edit opens a step picker: choose a step, then pick edit/delete/insert/move.
    This is the TUI path; the GUI bridges the same edit_plan function via
    gui_api.py (parity)."""
    from .ui import text_input, pager, flash, _cls, menu, confirm
    from . import diffview

    while True:
        if not diffview.confirm('', plan, title):
            # ESC: offer edit or discard
            choice = menu([('Edit plan', 'edit'), ('Discard plan', 'discard')],
                          'PLAN REJECTED')
            if choice == 'edit':
                plan = _edit_plan_ui(plan)
                if plan is None:
                    return None
                continue
            return None
        return plan


def _edit_plan_ui(plan):
    """Interactive step editor. Returns modified plan or None if cancelled."""
    from .ui import text_input, _cls, menu, confirm
    while True:
        idx, steps = _step_lines(plan)
        if not steps:
            flash("No numbered steps to edit", ok=False, secs=1.5)
            return plan
        items = [(f"{i + 1}. {ln.split('. ', 1)[-1] if '. ' in ln else ln}", i)
                 for i, ln in enumerate(steps)]
        items += [('─' * 30, None), ('Done editing', '__done__')]
        sel = menu(items, "EDIT PLAN — pick step")
        if sel is None or sel == '__done__':
            return plan
        step_idx = sel
        action = menu([('Edit step text', 'edit'),
                       ('Delete step', 'delete'),
                       ('Insert step before', 'insert'),
                       ('Move step', 'move'),
                       ('Cancel', 'cancel')],
                      f"STEP {step_idx + 1}")
        if action == 'cancel':
            continue
        if action in ('edit', 'insert'):
            prompt = 'New step text:' if action == 'edit' else 'Text for new step:'
            text = text_input(prompt)
            if not text:
                continue
        elif action == 'move':
            text = text_input(f'Move to position (1-{len(steps)}):')
            if not text:
                continue
            try:
                dst = int(text) - 1
            except ValueError:
                flash("Enter a number", ok=False, secs=1)
                continue
            text = str(dst)
        else:
            if not confirm(f"Delete step {step_idx + 1}?", danger=True):
                continue
            text = ''
        try:
            plan = edit_plan(plan, action, step_idx, text)
        except ValueError as e:
            flash(str(e), ok=False, secs=1.8)


def run(project_path, proj_folder, project_name, plan=None, per_step=False, should_cancel=None):
    """Interactive Plan→Execute flow. Returns True if an execution session was
    launched, False if cancelled.

    plan: pre-generated plan text (skip headless generation).
    per_step: confirm each step before executing.
    should_cancel: zero-arg callable checked before exec launch."""
    from .ui import text_input, pager, flash, _cls, menu, confirm
    from .config import load_settings, EFFORTS, EFFORT_LABELS, all_config_dirs, config_dir
    from . import diffview

    if should_cancel and should_cancel():
        return False

    s = load_settings()
    plan_model = s.get('plan_model', 'claude-opus-4-8')
    omni_env = _c.omniroute_env(s)
    if omni_env:
        from . import omniroute
        exec_model = s.get('omniroute_exec_model') or omniroute.AUTO_MODEL
        exec_via = 'OmniRoute (free tier)'
    else:
        exec_model = s.get('exec_model', 'claude-sonnet-5')
        exec_via = 'Anthropic'

    # effort matters most right here -- this is the one expensive reasoning
    # call in the whole flow, worth dialing up for a hard task
    effort = menu([(lbl, val) for val, lbl in zip(EFFORTS, EFFORT_LABELS)],
                  f"PLAN EFFORT  /  {plan_model}") or ''

    council_enabled = confirm("Optimize plan with a model council before executing? "
                              "(runs the draft past extra models, costs more tokens)")

    accounts = all_config_dirs()
    cfgdir = ''
    if len(accounts) > 1:
        items = [(f"{name}{'  (active)' if d == config_dir else ''}", d) for name, d in accounts]
        picked = menu(items, "ACCOUNT TO EXECUTE UNDER")
        if picked is None:
            return False
        cfgdir = picked if picked != config_dir else ''
        if cfgdir:
            from .paths import encode_component
            proj_folder = os.path.join(cfgdir, 'projects', encode_component(project_path))

    task = text_input(f"Task to plan ({plan_model} · {effort or 'default'} effort) "
                       f"then execute ({exec_model} via {exec_via}):")
    if not task:
        return False

    if plan:
        pass  # use the pre-supplied plan
    else:
        plan = _plan(task, plan_model, project_path, effort)
        if not plan:
            flash("Planning failed or cancelled", ok=False, secs=1.8)
            return False

        if council_enabled:
            _cls()
            via_note = ' via OmniRoute (free tier)' if omni_env else ''
            roster = OMNI_COUNCIL_MODELS if omni_env else COUNCIL_MODELS
            print(f"\n  Optimizing plan with model council ({', '.join(roster)}){via_note}...\n")
            plan = optimize_plan_council(task, plan, project_path, omni_env=omni_env)

    # per-step approval: show each step and let user approve/skip
    if per_step and plan:
        accepted = []
        for step_no, line in enumerate(plan.splitlines(), 1):
            if not line.strip() or not line.strip()[0].isdigit():
                continue
            if should_cancel and should_cancel():
                flash("Cancelled", ok=False, secs=1)
                return False
            ok = confirm(f"Step {step_no}: {line.strip()}")
            if ok:
                accepted.append(line)
        if not accepted:
            flash("No steps approved", ok=False, secs=1.5)
            return False
        plan = '\n'.join(accepted)
    elif should_cancel and should_cancel():
        return False

    # review / approve / edit loop (skip if plan was pre-supplied and per-step was done)
    if not per_step:
        plan = plan_review_loop(plan, f"PLAN ({plan_model}) — approve to execute")
        if plan is None:
            return False

    plan_path = write_plan_file(project_path, task, plan)
    if not plan_path:
        flash("Could not save plan", ok=False, secs=2)
        return False

    if should_cancel and should_cancel():
        return False

    if omni_env:
        from . import omniroute
        ok, msg = omniroute.ensure_running(s.get('omniroute_base_url', ''))
        if not ok:
            flash(f"OmniRoute: {msg}", ok=False, secs=2.5)
            return False

    args, env = build_exec_launch(project_path, proj_folder, task, exec_model, omni_env, cfgdir)
    if not args:
        flash("claude.exe not found", ok=False, secs=1.8)
        return False

    # Leave the alt screen before anything else owns the console (same as
    # main.py's _direct_launch) -- without this the exec session runs nested
    # inside the TUI's alternate screen buffer and comes back broken/blank.
    from . import render
    render.screen_restore()

    _cls()
    print(f"  Plan saved: {plan_path}")
    print(f"  Executing with {exec_model} via {exec_via} (planned by {plan_model})")
    print(f"  {'-' * 42}\n")
    try:
        subprocess.call(args, cwd=project_path, env=env)
    except Exception as e:
        print(f"\n  Execution launch failed: {e}")
        import time
        time.sleep(2)
    # Back to the TUI loop -- re-enter the alt screen so it resumes normally.
    render.screen_init()
    return True
