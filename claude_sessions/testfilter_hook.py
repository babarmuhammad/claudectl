"""Claude Code PreToolUse hook (matcher: Bash) — rewrite test-runner commands
to pipe through a failures-only filter (testfilter_filter.py) BEFORE the output
enters Claude's context. A green 2000-line test run collapses to a few summary
lines; failures pass through untouched. Saves thousands of context tokens per
test run. Stdlib-only; never errors; silently passes through anything it does
not recognize.
"""

import os
import re
import sys
import json

MARKER = 'claudectl-testfilter'

_RUNNERS = re.compile(
    r'^\s*(?:'
    r'python\s+-m\s+pytest|pytest|'
    r'npm\s+test|npx\s+(?:jest|vitest)|yarn\s+test|pnpm\s+test|'
    r'go\s+test|cargo\s+test'
    r')\b')


def main():
    try:
        data = json.load(sys.stdin)
        command = (data.get('tool_input') or {}).get('command', '')
    except Exception:
        return 0
    if (not command or MARKER in command
            or '|' in command or '>' in command       # already plumbed — hands off
            or not _RUNNERS.search(command)):
        return 0                                      # no output → tool runs as-is
    filt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'testfilter_filter.py')
    py = sys.executable.replace('\\', '/')
    fp = filt.replace('\\', '/')
    # runs inside Claude Code's Bash tool (Git Bash on Windows too)
    new_cmd = f'set -o pipefail; {command} 2>&1 | "{py}" "{fp}"  # {MARKER}'
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {"command": new_cmd}}}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
