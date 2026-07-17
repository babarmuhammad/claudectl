"""Multi-account manager — make running two (or more) Claude accounts easy.

Claude Code picks the account from its config dir (CLAUDE_CONFIG_DIR). Each
account = its own dir with its own login. claudectl stores named accounts, lets
you switch the active one, and — the point — **launch a second account in a new
terminal with one key** so both run at the same time.
"""

import os
import subprocess

from . import config as _c
from .ui import menu, text_input, flash, confirm, _cls, pause
from . import render


def _default_dir():
    return os.path.join(_c._USERPROFILE, '.claude')


def _accounts(s):
    """[(name, dir, is_active)] — always includes the built-in default account."""
    active = s.get('claude_config_dir', '') or ''
    out = [('default', '', not active)]                  # '' dir = ~/.claude
    for a in s.get('accounts', []):
        if isinstance(a, dict) and a.get('dir'):
            out.append((a.get('name', a['dir']), a['dir'],
                        os.path.expanduser(a['dir']) == os.path.expanduser(active)))
    return out


def _resolved(d):
    return os.path.expanduser(os.path.expandvars(d)) if d else _default_dir()


def accounts_menu():
    from .config import load_settings, save_settings
    while True:
        s = load_settings()
        accts = _accounts(s)
        items = []
        for name, d, active in accts:
            dot = f"{_c.C_OK}●{_c.C_RESET}" if active else '○'
            loc = d or '~/.claude'
            tag = f"  {_c.C_OK}(active){_c.C_RESET}" if active else ''
            items.append((f"{dot} {name}  {_c.C_DIM}{render.trunc(loc, 40)}{_c.C_RESET}{tag}",
                          f'acct:{name}'))
        items += [(f"{'─' * _c.W}", None),
                  ('＋  Add account', '__add__'),
                  ('Back', 'back')]
        sel = menu(items, "CLAUDE ACCOUNTS")
        if not sel or sel == 'back':
            return
        if sel == '__add__':
            _add_account(s)
        elif sel.startswith('acct:'):
            _account_actions(s, sel[5:])


def _add_account(s):
    from .config import save_settings
    name = text_input("Account name (e.g. work, personal):")
    if not name:
        return
    default_dir = os.path.join(_c._USERPROFILE, f'.claude-{name}')
    d = text_input("Config dir for this account:", default=default_dir)
    if not d:
        return
    rd = _resolved(d)
    try:
        os.makedirs(rd, exist_ok=True)
    except Exception as e:
        flash(f"Could not create dir: {e}", ok=False, secs=2)
        return
    accts = [a for a in s.get('accounts', []) if a.get('name') != name]
    accts.append({'name': name, 'dir': d})
    s['accounts'] = accts
    save_settings(s)
    if confirm(f"Log in to '{name}' now? (opens claude for /login)"):
        _login(d)
    else:
        flash(f"Account '{name}' added — log in later from its row", secs=1.6)


def _account_actions(s, name):
    from .config import save_settings
    d = '' if name == 'default' else next(
        (a['dir'] for a in s.get('accounts', []) if a.get('name') == name), '')
    acts = [('Switch active account (this claudectl)', 'switch'),
            ('Open in NEW terminal (run in parallel)', 'parallel'),
            ('Log in / re-login here', 'login')]
    if name != 'default':
        acts.append(('Rename', 'rename'))
        acts.append(('Remove from list', 'remove'))
    acts.append(('Cancel', 'cancel'))
    act = menu(acts, f"ACCOUNT  /  {name}")
    if act == 'switch':
        s['claude_config_dir'] = d
        save_settings(s)
        flash(f"Active account → {name}. Restart claudectl to fully apply.", secs=2)
    elif act == 'parallel':
        _open_terminal(d, name)
    elif act == 'login':
        _login(d)
    elif act == 'rename':
        new = text_input("New account name:", default=name)
        if new and new != name:
            if any(a.get('name') == new for a in s.get('accounts', [])) or new == 'default':
                flash(f"Name '{new}' already in use", ok=False, secs=1.8)
            else:
                for a in s.get('accounts', []):
                    if a.get('name') == name:
                        a['name'] = new
                save_settings(s)
                flash(f"Renamed '{name}' → '{new}' (config dir unchanged)", secs=1.8)
    elif act == 'remove':
        s['accounts'] = [a for a in s.get('accounts', []) if a.get('name') != name]
        if os.path.expanduser(s.get('claude_config_dir', '')) == os.path.expanduser(d):
            s['claude_config_dir'] = ''
        save_settings(s)
        flash(f"Removed '{name}' (its config dir on disk is untouched)", secs=1.8)


def _env_for(d):
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = _resolved(d)
    env.pop('ANTHROPIC_API_KEY', None)   # a set key would shadow the account login
    return env


def _login(d):
    exe = _c.get_claude_exe()
    if not exe:
        flash("claude.exe not found", ok=False, secs=1.6)
        return
    _cls()
    print(f"\n  Opening claude under {_resolved(d)}")
    print(f"  Use /login to sign in, then exit to return.\n")
    try:
        subprocess.call([exe], env=_env_for(d))
    except Exception as e:
        print(f"\n  Failed: {e}")
        pause("\n  Press Enter…")


def _open_terminal(d, name):
    """Launch claude for this account in a NEW terminal window so it runs
    alongside the current one (the easy 'two accounts at once')."""
    exe = _c.get_claude_exe()
    if not exe:
        flash("claude.exe not found", ok=False, secs=1.6)
        return
    try:
        # `start` opens a separate console; keep it open with cmd /k.
        # Pass argv as a list (no shell=True) so an account name containing
        # `"`, `&`, `|` etc. can't break out of the quoted title and run
        # arbitrary commands — Windows' list2cmdline quotes each arg safely.
        subprocess.Popen(['cmd', '/c', 'start', f'claude [{name}]', 'cmd', '/k', exe],
                         env=_env_for(d))
        flash(f"Opened a new terminal running claude as '{name}'", secs=1.8)
    except Exception as e:
        flash(f"Could not open terminal: {e}", ok=False, secs=2)
