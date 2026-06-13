"""Hooks manager — template, toggle, and remove Claude Code hooks in
settings.json (user scope ~/.claude/settings.json).

Hooks fire shell commands / prompts on tool events (PreToolUse, PostToolUse,
Stop, ...). This edits the `hooks` block; disabled hooks are parked under
`hooks_disabled` so they round-trip without losing config.
"""

import json
import os

from .config import W, config_dir
from .ui import menu, text_input, flash, pause, confirm, _cls
from . import config as _c
from . import render

settings_path = os.path.join(config_dir, 'settings.json')

# Ready-made hooks the user can drop in.
TEMPLATES = {
    'prettier-on-edit': {
        'event': 'PostToolUse',
        'entry': {'matcher': 'Edit|Write',
                  'hooks': [{'type': 'command', 'command': 'prettier --write "$FILE_PATH"'}]},
        'desc': 'Run prettier after every Edit/Write',
    },
    'block-curl': {
        'event': 'PreToolUse',
        'entry': {'matcher': 'Bash(curl:*)',
                  'hooks': [{'type': 'command', 'command': 'echo blocked && exit 1'}]},
        'desc': 'Block bash curl commands',
    },
    'notify-on-stop': {
        'event': 'Stop',
        'entry': {'hooks': [{'type': 'command',
                             'command': 'powershell -c "[console]::beep(800,200)"'}]},
        'desc': 'Beep when Claude finishes a turn',
    },
}


def _load():
    try:
        with open(settings_path, encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=2)
        return True
    except Exception:
        return False


def _count(block):
    return sum(len(v) if isinstance(v, list) else 0 for v in (block or {}).values())


def hooks_menu(scope=None):
    """List configured hooks; insert templates; toggle/remove."""
    while True:
        s = _load()
        hooks = s.get('hooks', {})
        disabled = s.get('hooks_disabled', {})
        items = []
        for event, entries in hooks.items():
            for i, e in enumerate(entries if isinstance(entries, list) else []):
                m = e.get('matcher', '(any)')
                items.append((f"{_c.C_OK}●{_c.C_RESET} {event}  {_c.C_DIM}{m}{_c.C_RESET}",
                              f'on:{event}:{i}'))
        for event, entries in disabled.items():
            for i, e in enumerate(entries if isinstance(entries, list) else []):
                m = e.get('matcher', '(any)')
                items.append((f"{_c.C_DIM}○ {event}  {m} (disabled){_c.C_RESET}",
                              f'off:{event}:{i}'))
        if not items:
            items.append((f"{_c.C_DIM}(no hooks configured){_c.C_RESET}", None))
        items += [(f"{'─' * W}", None),
                  ('＋  Add from template', '__tpl__'),
                  ('📝  Edit settings.json', '__edit__')]

        sel = menu(items, f"HOOKS  /  {os.path.basename(config_dir)}")
        if not sel:
            return
        if sel == '__edit__':
            from .config import open_in_editor
            if not os.path.exists(settings_path):
                _save(_load())
            open_in_editor(settings_path)
        elif sel == '__tpl__':
            _add_template()
        elif sel.startswith(('on:', 'off:')):
            _toggle_or_remove(sel)


def _add_template():
    pick = menu([(f"{k}  —  {v['desc']}", k) for k, v in TEMPLATES.items()],
                "HOOK TEMPLATES")
    if not pick:
        return
    tpl = TEMPLATES[pick]
    s = _load()
    s.setdefault('hooks', {}).setdefault(tpl['event'], []).append(tpl['entry'])
    if _save(s):
        flash(f"Added {pick}")
    else:
        flash("Write failed", ok=False, secs=1.4)


def _toggle_or_remove(sel):
    state, event, idx = sel.split(':')
    idx = int(idx)
    act = menu([('Toggle enabled/disabled', 'toggle'),
                ('Remove', 'remove'), ('Cancel', 'cancel')], "HOOK")
    if act not in ('toggle', 'remove'):
        return
    s = _load()
    src_key = 'hooks' if state == 'on' else 'hooks_disabled'
    dst_key = 'hooks_disabled' if state == 'on' else 'hooks'
    src = s.get(src_key, {})
    entries = src.get(event, [])
    if idx >= len(entries):
        return
    entry = entries.pop(idx)
    if not entries:
        src.pop(event, None)
    if act == 'toggle':
        s.setdefault(dst_key, {}).setdefault(event, []).append(entry)
        flash("Toggled")
    else:
        if not confirm("Remove this hook?", danger=True):
            return  # don't persist the pop
        flash("Hook removed")
    _save(s)
