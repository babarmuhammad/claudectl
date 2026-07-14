"""stdin→stdout failures-only filter for test-runner output (see
testfilter_hook.py). Keeps failure lines, everything from a failure section on,
and the final summary; suppresses pass noise. Never errors; exit 0 always so
the test command's own exit code (via pipefail) is what Claude sees.
"""

import re
import sys

TAIL_KEEP = 10          # always keep the last N lines (summary)
MAX_LINES = 400         # hard cap on kept body lines

_FAIL = re.compile(
    r'(FAILED|ERROR(S)?\b|error(\[|:)|--- FAIL|^FAIL\b|AssertionError|Traceback'
    r'|panic:|Exception\b|failed\b)', re.I)
_SECTION = re.compile(r'=+ (FAILURES|ERRORS) =+')
# a passing/progress line: says PASSED/ok/skipped or is just a dots-progress bar
_PASS = re.compile(r'(\b(PASSED|PASS|ok|SKIPPED|xfail)\b|^\s*\.+\s*(\[\s*\d+%\])?\s*$)', re.I)


def main():
    lines = sys.stdin.read().splitlines()
    kept, in_fail_section = [], False
    for ln in lines[:-TAIL_KEEP] if len(lines) > TAIL_KEEP else []:
        if _SECTION.search(ln):
            in_fail_section = True
        if _FAIL.search(ln):
            kept.append(ln)                          # failures always kept
        elif _PASS.search(ln):
            continue                                 # drop pass/progress noise anywhere
        elif in_fail_section:
            kept.append(ln)                          # traceback body inside the section
    kept = kept[-MAX_LINES:]
    tail = lines[-TAIL_KEEP:] if len(lines) > TAIL_KEEP else lines
    suppressed = len(lines) - len(kept) - len(tail)
    out = kept + tail
    if suppressed > 0:
        out.append(f'[claudectl testfilter: {suppressed} passing/noise lines suppressed]')
    sys.stdout.write('\n'.join(out) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
