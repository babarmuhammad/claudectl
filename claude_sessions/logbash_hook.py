"""Claude Code hook — records what this project ran, and what it was refused.

Two bindings, two logs, and the split matters:

- **PostToolUse** appends the Bash command to `.claudectl/bash-log.txt`. That
  feeds `health.frequent_bash_commands`, whose question is "what does this
  project run a lot" — an allowlist candidate list.
- **PermissionDenied** appends a structured record to `.claudectl/denied.jsonl`.

Both used to write the same bash log, which made a denial indistinguishable
from a success in the one file that claimed to teach the deny-rule generator
"from real ones" — and dropped every non-Bash denial entirely, since it only
ever read `tool_input.command`. Run with `--denied` for the second binding.
"""

import sys
import os
import json

# Runs as a plain script (hooks.py spawns `"<python>" "<abs path>"`), so there
# is no package context — the path bootstrap is what makes `from claude_sessions
# import ...` work below. Same pattern as recall_hook.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Claude Code captures stdout as a PIPE, so CPython picks the locale
# codepage (cp1252 on Windows) and any non-ASCII character in the payload
# either mojibakes or raises — silently losing the whole hook output.
# Guarded: `sys.stdout` is None in a windowed process (pythonw with no
# console). A hook must degrade to plain output, never die at import.
try:
    sys.stdout.reconfigure(encoding='utf-8')
except (AttributeError, ValueError, OSError):
    pass


def _command_of(data):
    """The command, for Bash — else the most identifying field the tool has.
    A denial on Read(.env) or WebFetch is worth recording too, and recording it
    as '' would make every non-Bash denial look like the same event."""
    ti = data.get('tool_input') or {}
    if not isinstance(ti, dict):
        return ''
    for key in ('command', 'file_path', 'path', 'url', 'pattern'):
        v = ti.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    denied = '--denied' in argv
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    cwd = data.get('cwd') or os.getcwd()
    if denied:
        # the whole event, not just its command: the tool name is what tells a
        # blocked Read from a blocked Bash, and the reason is what tells you
        # which fix applies (an allow rule, or an autoMode.environment entry)
        from claude_sessions.automode import record
        record(cwd, str(data.get('tool_name') or ''), _command_of(data),
               str(data.get('permission_decision_reason')
                   or data.get('reason') or ''))
        return 0
    cmd = str((data.get('tool_input') or {}).get('command', '')).strip()
    if not cmd:
        return 0
    try:
        from claude_sessions import store
        d = store.claudectl_dir(cwd)
        p = os.path.join(d, 'bash-log.txt')
        with open(p, 'a', encoding='utf-8') as f:
            f.write(cmd + '\n')
        _rotate(p)
    except Exception:
        pass
    return 0


#: this file had grown to 90 KB unbounded. Bounded in BYTES rather than lines:
#: a bash command has no characteristic length, and bytes is what the problem
#: actually was.
_MAX_BYTES = 64 * 1024
_KEEP_BYTES = _MAX_BYTES // 2


def _rotate(path):
    """Drop the oldest half once the log passes _MAX_BYTES. Size-gated so the
    common append does no extra I/O, and the trim reads only the tail — this
    runs inside a PreToolUse hook, on the turn's critical path."""
    try:
        if os.path.getsize(path) <= _MAX_BYTES:
            return
        with open(path, 'rb') as f:
            f.seek(-_KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        # the seek lands mid-line; drop that partial first line
        tail = tail.split(b'\n', 1)[1] if b'\n' in tail else tail
        with open(path, 'wb') as f:
            f.write(tail)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
