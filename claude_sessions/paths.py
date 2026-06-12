import os
import re


# ── path resolution ──────────────────────────────────────────

# Claude Code encodes each path component with /[^a-zA-Z0-9]/g -> '-'
# (verified against claude.exe). ASCII-only: dots, spaces, _, +, #, parens,
# AND non-ASCII letters (accents, CJK) all collapse to '-'. Mirror it
# exactly — Python's str.isalnum() is Unicode-aware and would wrongly keep
# accented chars, so use an explicit ASCII class instead.
_NON_ALNUM = re.compile(r'[^a-zA-Z0-9]')


def encode_component(name):
    return _NON_ALNUM.sub('-', name)


def find_actual_path(encoded, max_depth=8):
    if '--' not in encoded:
        return None
    drive_part, rest = encoded.split('--', 1)
    base = drive_part + ':\\'
    if not os.path.exists(base):
        return None

    def match(current, remaining, depth):
        if not remaining:
            return current
        if depth > max_depth:
            return None
        try:
            subdirs = [d for d in os.listdir(current)
                       if os.path.isdir(os.path.join(current, d))]
        except (PermissionError, OSError):
            return None
        rem_l = remaining.lower()
        for subdir in subdirs:
            enc = encode_component(subdir).lower()   # NTFS is case-insensitive
            if enc == rem_l:
                return os.path.join(current, subdir)
            if rem_l.startswith(enc + '-'):
                r = match(os.path.join(current, subdir), remaining[len(enc)+1:], depth+1)
                if r:
                    return r
        return None

    return match(base, rest, 0)
