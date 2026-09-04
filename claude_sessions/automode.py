"""Auto mode: read the classifier's effective config, and teach it about this
machine's infrastructure.

Auto mode runs every action past a classifier model instead of past the user.
By default that classifier trusts only the working directory and the remotes
the repo already had when the session started; everything else is "external",
which is why routine internal work gets blocked until you tell it otherwise.
The `autoMode` settings block is how you tell it.

Three constraints shape this module:

- **User scope only.** The classifier deliberately does NOT read `autoMode`
  from a project's `.claude/settings.json` or `.claude/settings.local.json` —
  both live in the repo, so a checked-in file (or a build step) could otherwise
  inject its own allow rules. Everything here writes the ACCOUNT's settings.json
  through `hooks._load`/`_save`, which is the scope where it takes effect.

- **`"$defaults"` is load-bearing.** Setting `environment`, `allow`,
  `soft_deny` or `hard_deny` WITHOUT that literal string replaces the entire
  built-in list for that section — dropping it from `soft_deny` discards the
  force-push, `curl | bash` and production-deploy rules in one edit. Every
  writer here keeps it, and `set_environment` puts it back if it went missing.

- **The CLI is the source of truth for what the rules ARE.** `claude auto-mode
  defaults|config` print them as JSON; this module never keeps its own copy,
  because a hand-maintained copy of something the tool already states is wrong
  the first time the tool changes.
"""

import json
import os
import time

from . import hooks
from . import proc

__all__ = ['config_json', 'defaults_json', 'reset', 'environment',
           'set_environment', 'default_mode', 'set_default_mode',
           'denials', 'denials_path', 'summarise']

#: `claude auto-mode <sub>` is a local read; it should never hang a UI thread
_TIMEOUT = 25


def _claude():
    from .config import get_claude_exe
    return get_claude_exe()


def _run(sub, cfgdir=None, extra=()):
    """`claude auto-mode <sub>` → (ok, parsed_json_or_error_string)."""
    exe = _claude()
    if not exe:
        return False, 'claude.exe not found'
    env = os.environ.copy()
    if cfgdir:
        env['CLAUDE_CONFIG_DIR'] = cfgdir
    r = proc.run([exe, 'auto-mode', sub, *extra], env=env, timeout=_TIMEOUT)
    if r is None:
        return False, 'auto-mode %s did not complete' % sub
    out = (getattr(r, 'stdout', '') or '').strip()
    if getattr(r, 'returncode', 1) != 0:
        err = (getattr(r, 'stderr', '') or '').strip() or out
        return False, err[:400] or ('auto-mode %s failed' % sub)
    try:
        return True, json.loads(out)
    except Exception:
        # `reset` prints a summary, not JSON — a non-JSON success is still a
        # success, and the caller gets the text
        return True, out


def config_json(cfgdir=None):
    """The rules the classifier ACTUALLY uses: your settings applied where set,
    built-in defaults everywhere else, with "$defaults" already expanded."""
    return _run('config', cfgdir)


def defaults_json(cfgdir=None):
    """The built-in rules, before any of your settings."""
    return _run('defaults', cfgdir)


def reset(cfgdir=None):
    """Remove the `autoMode` section from this account's settings.json, via the
    CLI's own subcommand so its notion of "reset" stays authoritative. Rules
    from managed settings are unaffected — the CLI says so, and so do we."""
    return _run('reset', cfgdir, extra=('--yes',))


# ── the settings block ───────────────────────────────────────

DEFAULTS_SENTINEL = '$defaults'


def environment(cfgdir=None):
    """The account's own `autoMode.environment` entries (NOT the effective
    list — use config_json for that)."""
    am = hooks._load(cfgdir).get('autoMode')
    env = (am or {}).get('environment')
    return [str(e) for e in env] if isinstance(env, list) else []


def set_environment(entries, cfgdir=None):
    """Replace `autoMode.environment`, keeping "$defaults" at the front.

    Returns (ok, message). Entries are PROSE, not patterns — the classifier
    reads them as natural language, so they are stored verbatim apart from
    stripping and de-duplication.
    """
    clean = []
    for e in entries or []:
        e = str(e).strip()
        if e and e != DEFAULTS_SENTINEL and e not in clean:
            clean.append(e)
    s = hooks._load(cfgdir)
    am = dict(s.get('autoMode') or {})
    if not clean:
        am.pop('environment', None)
    else:
        # without the sentinel the built-in trust slots are DISCARDED, and the
        # classifier stops trusting even the working repo's own remotes
        am['environment'] = [DEFAULTS_SENTINEL] + clean
    if am:
        s['autoMode'] = am
    else:
        s.pop('autoMode', None)
    ok = bool(hooks._save(s, cfgdir))
    return ok, ('saved %d entry(ies)' % len(clean)) if ok else 'could not write settings.json'


def default_mode(cfgdir=None):
    """`permissions.defaultMode` for this account, or ''."""
    perms = hooks._load(cfgdir).get('permissions')
    v = (perms or {}).get('defaultMode')
    return str(v) if isinstance(v, str) else ''


def set_default_mode(mode, cfgdir=None):
    """Write (or clear) `permissions.defaultMode`. Routed through ccsettings so
    the enum is validated and the sibling allow/deny/ask rules survive."""
    from . import ccsettings
    return ccsettings.write('permissions.defaultMode', mode, cfgdir)


# ── denials ──────────────────────────────────────────────────
# Claude Code records blocked actions under /permissions → Recently denied, and
# fires a PermissionDenied hook. claudectl's hook writes them here so the
# allowlist and environment proposals can be driven by what ACTUALLY got
# blocked rather than by what someone guessed would.

def denials_path(project_path):
    return os.path.join(project_path, '.claudectl', 'denied.jsonl')


def denials(project_path, limit=50):
    """Most recent first. Each: {ts, tool, command, reason}."""
    p = denials_path(project_path)
    if not os.path.isfile(p):
        return []
    out = []
    try:
        from . import transcripts
        for obj in transcripts.iter_json(p):
            if isinstance(obj, dict):
                out.append(obj)
    except Exception:
        return []
    return list(reversed(out))[:limit]


def summarise(project_path, limit=50):
    """Denials grouped by what was blocked, commonest first — the shape the
    question actually has ("what keeps getting blocked?"), not a raw log.

    Returns [{'key','tool','count','last','reason','samples':[cmd,...]}].
    """
    groups = {}
    for d in denials(project_path, limit=limit):
        tool = str(d.get('tool') or '?')
        cmd = str(d.get('command') or '')
        # a Bash denial groups by the command's first word; every other tool
        # groups by the tool itself, because the target is the whole story
        key = (tool + ':' + cmd.split()[0]) if (tool == 'Bash' and cmd.split()) else tool
        g = groups.setdefault(key, {'key': key, 'tool': tool, 'count': 0,
                                    'last': 0, 'reason': '', 'samples': []})
        g['count'] += 1
        try:
            g['last'] = max(g['last'], float(d.get('ts') or 0))
        except (TypeError, ValueError):
            pass
        if not g['reason'] and d.get('reason'):
            g['reason'] = str(d['reason'])[:200]
        if cmd and len(g['samples']) < 3 and cmd not in g['samples']:
            g['samples'].append(cmd[:200])
    return sorted(groups.values(), key=lambda g: (-g['count'], -g['last']))


def record(project_path, tool, command, reason):
    """Append one denial. Called by the hook; kept here so the format has one
    writer and one reader."""
    try:
        from . import store
        d = store.claudectl_dir(project_path)
        line = json.dumps({'ts': round(time.time()), 'tool': tool or '',
                           'command': (command or '')[:500],
                           'reason': (reason or '')[:300]}, ensure_ascii=False)
        p = os.path.join(d, 'denied.jsonl')
        with open(p, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        _rotate(p)
        return True
    except Exception:
        return False


#: bounded in BYTES, same reasoning as logbash_hook's bash log: a denial record
#: has no characteristic length, and unbounded growth was the actual problem
_MAX_BYTES = 64 * 1024
_KEEP_BYTES = _MAX_BYTES // 2


def _rotate(path):
    """Drop the oldest half once the log passes _MAX_BYTES. Size-gated, so the
    common append does no extra I/O — this runs inside a hook, on the turn's
    critical path."""
    try:
        if os.path.getsize(path) <= _MAX_BYTES:
            return
        with open(path, 'rb') as f:
            f.seek(-_KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        tail = tail.split(b'\n', 1)[1] if b'\n' in tail else tail
        with open(path, 'wb') as f:
            f.write(tail)
    except Exception:
        pass
