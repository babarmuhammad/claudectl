"""Claude Code SessionStart hook — inject a compact code-minimization rule as
additionalContext so the agent avoids over-engineering (fewer generated tokens).
Shell-agnostic; never errors. Inspired by Ponytail
(https://github.com/DietrichGebert/ponytail).
"""

import sys
import json

_RULE = (
    "Code minimization (claudectl): read/understand fully, then write the LEAST "
    "code that works. Before writing, stop at the first hit — 1) needed at all? "
    "(YAGNI) 2) already in this repo? reuse 3) stdlib? 4) native platform feature? "
    "5) an installed dependency? 6) one line? 7) only then the minimum that works. "
    "No speculative abstraction or dead scaffolding; keep readability and full safety."
)


def main():
    try:
        json.load(sys.stdin)          # consume hook input (ignored)
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": _RULE}}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
