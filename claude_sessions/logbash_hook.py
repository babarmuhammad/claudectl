"""Claude Code PostToolUse hook — append the Bash command to
.claudectl/bash-log.txt (in the project cwd). Shell-agnostic; never errors.
"""

import sys
import os
import json


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    cmd = str((data.get('tool_input') or {}).get('command', '')).strip()
    if not cmd:
        return 0
    cwd = data.get('cwd') or os.getcwd()
    try:
        d = os.path.join(cwd, '.claudectl')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'bash-log.txt'), 'a', encoding='utf-8') as f:
            f.write(cmd + '\n')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
