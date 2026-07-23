# Plan→Execute Audit — claude_sessions/plan_execute.py

## Key functions
- **Plan generator**: `_plan(task, plan_model, cwd, effort='')` — runs headless `claude -p` with `--disallowedTools Write,Edit,NotebookEdit,Bash`; returns plan markdown string or `''`
- **Executor**: `run(project_path, proj_folder, project_name)` — the TUI interactive flow; calls `build_exec_launch()` to assemble launch args then `subprocess.call()` to run the exec session
- **Build launch args**: `build_exec_launch(project_path, proj_folder, task, exec_model, omni_env=None, cfgdir='')` — returns `(args, env)` tuple; this is what the GUI also calls
- **Approval gate**: `plan_review_loop(plan, title='')` uses `diffview.confirm()` for approve/reject and `_edit_plan_ui()` for step-level editing
- **Edit plan (pure)**: `edit_plan(plan, action, index=None, text='')` — pure string manipulation: edit/delete/insert/move steps
- **Plan step data shape**: plan is a **markdown string**, not a list of dicts. Steps are lines matching `r'^\d+\.\s'` in the text. `_step_lines(plan)` returns `(indices, lines)` where indices are line positions in the splitlines output.
- **Council**: `optimize_plan_council(task, plan, cwd, models=None, omni_env=None)` — fans draft out to N extra models for critique, then synthesizes

## Current limitations
1. **No per-step approval** — entire plan approved/rejected as one unit; cannot gate execution step-by-step
2. **No cancel mid-run** — once `subprocess.call()` is invoked, no cooperative cancel hook; user must kill the process externally
3. **No persistence/resume** — plan is written to `plan-latest.md` but there is no `save_plan`/`load_plan` mechanism to reload an edited plan into the GUI; plan edits are TUI-only
4. **No re-plan** — no mechanism to regenerate the plan with feedback appended to the prompt; user must restart the whole flow
5. **No plan injection into executor** — `run()` always calls `_plan()` internally; external callers (GUI) cannot pass a pre-approved/edited plan to the executor without the generator
6. **No GUI plan editing** — `_edit_plan_ui()` is TUI-only; the GUI can only trigger the full `plan_launch` job which bakes the plan at generation time
