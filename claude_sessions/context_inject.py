"""Start a new session seeded with another session's transcript as context —
including a session that lives under a DIFFERENT account. Mirrors
plan_execute.py's proven pattern: write the full content to disk, hand the
new session a short --append-system-prompt pointer (avoids the Windows argv
length limit — the model reads the full file with its own tools)."""

import os
import subprocess

from .paths import encode_component

CTX_FILE = os.path.join('.claudectl', 'injected-context.md')


def _account_dir_of(proj_folder):
    """The config dir a project folder lives under (proj_folder is
    <cfgdir>/projects/<encoded-name>)."""
    return os.path.dirname(os.path.dirname(proj_folder))


def _account_name_for(acct_dir):
    from .config import all_config_dirs
    target = os.path.normcase(os.path.abspath(acct_dir))
    for name, d in all_config_dirs():
        if os.path.normcase(os.path.abspath(d)) == target:
            return name
    return os.path.basename(acct_dir)


def find_sessions_across_accounts(project_path):
    """[(acct_name, folder, sid, mtime, preview, title)] for every session of
    this project under every known account, newest-first."""
    from .config import all_config_dirs
    from .sessions import scan_sessions, load_name, get_session_title

    encoded = encode_component(project_path)
    out = []
    for name, acct_dir in all_config_dirs():
        folder = os.path.join(acct_dir, 'projects', encoded)
        for mtime, sid, preview, _count in scan_sessions(folder):
            title = (load_name(folder, sid)
                     or get_session_title(os.path.join(folder, f"{sid}.jsonl")) or '')
            out.append((name, folder, sid, mtime, preview, title))
    out.sort(key=lambda r: r[3], reverse=True)
    return out


def _write_context_file(project_path, folder, sid, acct_name):
    from .transcript import iter_transcript
    from .stats import get_session_stats_cached
    from .sessions import load_name

    jsonl = os.path.join(folder, f"{sid}.jsonl")
    msgs  = iter_transcript(jsonl)
    stats = get_session_stats_cached(jsonl)
    name  = load_name(folder, sid)
    title = name or stats.get('title') or sid[:8]

    out = [f"# Prior session context — {title}", f"(account: {acct_name})", '']
    for m in msgs:
        out.append(f"### {'User' if m['role'] == 'user' else 'Assistant'}")
        out.append('')
        out.append(m['text'])
        out.append('')

    ctx_path = os.path.join(project_path, CTX_FILE)
    os.makedirs(os.path.dirname(ctx_path), exist_ok=True)
    with open(ctx_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')
    return ctx_path, title


def _pick_target_account(proj_folder):
    """Choose the account the NEW session launches under. Defaults to the
    current project's account; only prompts when >1 account exists.
    Returns (acct_dir, acct_name) or (None, None) if cancelled."""
    from .ui import menu
    from .config import all_config_dirs

    current = os.path.normcase(os.path.abspath(_account_dir_of(proj_folder)))
    accts = list(all_config_dirs())
    if len(accts) <= 1:
        d = _account_dir_of(proj_folder)
        return d, _account_name_for(d)
    # current account first so ENTER keeps today's behaviour
    accts.sort(key=lambda t: os.path.normcase(os.path.abspath(t[1])) != current)
    items = [(f"[{name}]  {d}", (name, d)) for name, d in accts]
    items.append(('Cancel', None))
    pick = menu(items, "LAUNCH NEW SESSION UNDER WHICH ACCOUNT?")
    if pick is None:
        return None, None
    name, d = pick
    return d, name


def run(project_path, proj_folder, project_name):
    """Pick a source session (any account, this project) and launch a NEW
    session — under an account of the user's choosing — seeded with its
    transcript. Returns True if a session was launched, False if cancelled."""
    from .ui import menu, flash, _cls
    from .config import get_claude_exe, load_settings
    from .sessions import load_add_dirs, read_extra_paths

    candidates = find_sessions_across_accounts(project_path)
    if not candidates:
        flash("No sessions found for this project under any account", ok=False, secs=2)
        return False

    items = []
    for i, (acct_name, folder, sid, mtime, preview, title) in enumerate(candidates):
        label = title or preview or sid[:8]
        items.append((f"[{acct_name}]  {label}", i))
    items.append(('Cancel', None))
    pick = menu(items, "INJECT CONTEXT FROM SESSION")
    if pick is None:
        return False

    acct_name, folder, sid, _mtime, _preview, _title = candidates[pick]

    # choose the TARGET account the new session is created under (may differ
    # from both the source session's account and the current one)
    target_dir, target_name = _pick_target_account(proj_folder)
    if target_dir is None:
        return False
    encoded = encode_component(project_path)
    target_folder = os.path.join(target_dir, 'projects', encoded)

    ctx_path, title = _write_context_file(project_path, folder, sid, acct_name)

    exe = get_claude_exe()
    if not exe:
        flash("claude.exe not found", ok=False, secs=1.8)
        return False

    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = target_dir
    extra = read_extra_paths(target_folder)
    if extra:
        env['PATH'] = ';'.join(extra) + ';' + env.get('PATH', '')

    settings = load_settings()
    model = settings.get('default_model', '')

    pointer = (f"Prior conversation context (from the '{acct_name}' account, session "
               f"'{title}') is saved at {CTX_FILE.replace(os.sep, '/')}. Read it first "
               f"for background, then continue from where the user picks up.")
    args = [exe, '--append-system-prompt', pointer]
    if model:
        args += ['--model', model]
    sp_file = os.path.join(target_folder, 'system-prompt.txt') if target_folder else ''
    if sp_file and os.path.isfile(sp_file):
        args += ['--system-prompt-file', sp_file]
    add_dirs = [d for d in load_add_dirs(target_folder) if os.path.isdir(d)]
    if add_dirs:
        args += ['--add-dir', *add_dirs]

    _cls()
    print(f"  Context: {ctx_path}")
    print(f"  {'-' * 42}\n")
    try:
        subprocess.call(args, cwd=project_path, env=env)
    except Exception as e:
        print(f"\n  Launch failed: {e}")
    return True
