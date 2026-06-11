import os


# ── path resolution ──────────────────────────────────────────

def encode_component(name):
    return ''.join('-' if c in '_+-#' else c for c in name)


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
        for subdir in subdirs:
            enc = encode_component(subdir)
            if enc == remaining:
                return os.path.join(current, subdir)
            if remaining.startswith(enc + '-'):
                r = match(os.path.join(current, subdir), remaining[len(enc)+1:], depth+1)
                if r:
                    return r
        return None

    return match(base, rest, 0)
