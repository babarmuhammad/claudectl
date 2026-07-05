"""Claude Code PreToolUse guard — block a tool call when a field of its input
matches a regex. Shell-agnostic (runs as a Python script, not a shell snippet,
so it works whether Claude Code invokes hooks via bash, cmd, or PowerShell).

    <python> guard_hook.py <field> <regex> [message]

Exit 2 = block the tool (Claude sees the message on stderr). Any error → exit 0
(never wrongly block). Used by the block-* hook templates in hooks.py.
"""

import sys
import json
import re


def main():
    if len(sys.argv) < 3:
        return 0
    field, pattern = sys.argv[1], sys.argv[2]
    msg = sys.argv[3] if len(sys.argv) > 3 else 'blocked by claudectl'
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    val = str((data.get('tool_input') or {}).get(field, ''))
    try:
        hit = re.search(pattern, val)
    except re.error:
        return 0
    if hit:
        sys.stderr.write('claudectl: ' + msg + '\n')
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
