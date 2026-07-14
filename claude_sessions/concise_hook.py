"""Claude Code SessionStart hook — inject a concise-output rule as
additionalContext so the agent spends fewer OUTPUT tokens per turn (no
narration, no re-printed code). Tiny always-on input cost, net-negative on
tokens. Shell-agnostic; never errors.
"""

import sys
import json

_RULE = (
    "Concise output (claudectl): answer directly — no preamble, no narration of "
    "what you are about to do, no recap of what you just did. Never re-print "
    "unchanged code; reference file:line instead. Explain only what was asked, "
    "at the depth asked. Skip closing summaries when the result is visible from "
    "the change itself. Prefer editing files over printing their content."
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
