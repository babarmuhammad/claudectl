"""Loops: start one, watch it, end it — in a session, or with nothing open.

TWO KINDS, BECAUSE CLAUDE CODE ONLY OFFERS ONE
----------------------------------------------
`/loop` is a bundled Claude Code skill and its tasks are **session-scoped**:
they live in one conversation, fire only while that session is open and idle,
die with it, and expire after seven days. That is the whole contract — there is
no daemon to talk to and no documented state file to poll.

  **kind='session'** — what `/loop` really is. claudectl starts it by launching a
  session whose first typed message is `/loop …` (`claude "<prompt>"` submits it
  and stays interactive), watches it through that session's own transcript (each
  iteration is a turn), and "stops" it by ending the session, because from
  outside the session that is the only lever there is.

  **kind='schedule'** — the answer to "I do not want to keep a session open".
  claudectl registers an entry in the OS scheduler (Task Scheduler on Windows,
  cron elsewhere) that runs `claude -p` headless on the interval, in the project,
  under the account you picked. It keeps running with claudectl closed and no
  session anywhere. This is claudectl doing locally what Claude Code's own
  comparison table calls a Desktop scheduled task.

WHY A SCHEDULED RUN IS FRESH, AND HOW IT STILL REMEMBERS
--------------------------------------------------------
Every scheduled run is a NEW session. Resuming one forever would grow its
context (and its cost) without bound. What makes it a loop rather than a
repeated one-shot is `CLAUDECTL:LOOP`: a sentinel block in the project's
CLAUDE.md holding the last few outcomes, **rewritten** on every run so it can
never grow. CLAUDE.md is read on every turn, so the next run starts knowing what
the previous ones did.

THE GUARDRAILS ARE IN THE RUNNER, NOT THE UI
--------------------------------------------
A forgotten scheduled task spends money forever. So the expiry is enforced by
the thing the scheduler executes — it unschedules itself — and the per-run
budget cap rides on every invocation.

`loop.md` is a different thing that shares the name: the default prompt a bare
`/loop` uses, at `<project>/.claude/loop.md` (wins) or `<account>/loop.md`.
"""

import json
import os
import re
import sys
import time
import uuid

from . import config as _c
from . import jsonstore
from . import proc
from . import store

__all__ = ['registry_path', 'record', 'listing', 'stop', 'loop_prompt',
           'schedule', 'unschedule', 'run_once', 'renew', 'task_name',
           'MAX_KEPT', 'MAX_JOURNAL', 'DEFAULT_TTL', 'PERMS']

#: finished loops are kept so the board can show what ran, but not forever
MAX_KEPT = 40

#: outcomes kept per loop for the board. The CLAUDE.md block shows fewer still —
#: see JOURNAL_IN_MD — because that one is paid for on every turn of every
#: session in the project, not just when the board is open.
MAX_JOURNAL = 20
JOURNAL_IN_MD = 5

#: Claude Code expires its own scheduled tasks after seven days. A background
#: loop is the same hazard with a longer reach (it survives reboots), so it
#: inherits the same bound — renewable, never silent.
DEFAULT_TTL = 7 * 86400

#: `claude -p` starts in Manual mode on every plan, so an unattended run does
#: NOTHING unless it is told what it may do. Each of these is a real answer, and
#: the picker states the consequence rather than the name.
PERMS = [
    ('auto', 'a classifier reviews each action instead of you'),
    ('acceptEdits', 'writes files without asking; shell and network still gated'),
    ('dontAsk', 'denies anything outside your allow rules — it can report, not change'),
]


def registry_path(cfgdir=None):
    return os.path.join(cfgdir or _c.config_dir, 'claudectl-loops.json')


def _load(cfgdir=None):
    d = jsonstore.load(registry_path(cfgdir), expect=dict)
    rows = d.get('loops')
    return rows if isinstance(rows, list) else []


def _save(rows, cfgdir=None):
    return _c.write_json_atomic(registry_path(cfgdir), {'loops': rows[-MAX_KEPT:]})


def loop_prompt(interval='', prompt=''):
    """The message to type. Mirrors the documented `/loop` grammar exactly:

      interval + prompt → fixed schedule
      prompt only       → Claude picks the delay each iteration
      interval only     → the built-in maintenance prompt, or your loop.md
      neither           → the same, self-paced
    """
    return ' '.join(p for p in ('/loop', (interval or '').strip(),
                                (prompt or '').strip()) if p)


def record(path, encoded, cfgdir, interval, prompt, pid, name='',
           kind='session', perm='auto', ttl=DEFAULT_TTL):
    """Note a loop claudectl just started. Returns the row."""
    row = {'id': uuid.uuid4().hex[:8], 'kind': kind,
           'path': path, 'encoded': encoded, 'cfgdir': cfgdir or '',
           'name': name or os.path.basename(path.rstrip('\\/')) or path,
           'interval': interval or '', 'prompt': prompt or '',
           'text': loop_prompt(interval, prompt),
           'perm': perm or 'auto',
           'started': time.time(), 'expires': time.time() + (ttl or DEFAULT_TTL),
           'runs': 0, 'last_run': 0.0, 'last_cost': 0.0, 'last_result': '',
           'last_error': '', 'journal': [],
           'pid': int(pid or 0), 'stopped': 0.0}
    rows = _load(cfgdir)
    rows.append(row)
    _save(rows, cfgdir)
    return row


def _newest_transcript(row):
    """The session file that loop is most likely writing into.

    claudectl cannot know the session id it just created — Claude Code mints it
    — so the newest transcript in the project folder that has been touched since
    the loop started is the honest answer, and none is reported when nothing
    has been."""
    try:
        folder = store.project_folder(row.get('cfgdir') or None, row['encoded'])
    except Exception:
        return None
    best, best_m = None, 0
    try:
        for fn in os.listdir(folder):
            if not fn.endswith('.jsonl'):
                continue
            p = os.path.join(folder, fn)
            m = os.path.getmtime(p)
            if m >= row['started'] - 5 and m > best_m:
                best, best_m = p, m
    except OSError:
        return None
    return (best, best_m) if best else None


def _turns_since(jsonl, since):
    """Assistant turns written after *since* — one per loop iteration, plus the
    turns you type yourself. Counted from the transcript because that is the
    only record of a fire that exists outside the session."""
    from . import transcripts
    n = 0
    for obj in transcripts.iter_json(jsonl, prefilter='"assistant"'):
        if obj.get('type') != 'assistant':
            continue
        ts = obj.get('timestamp') or ''
        if not ts:
            continue
        try:
            import datetime as _dt
            t = _dt.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        except ValueError:
            continue
        if t >= since:
            n += 1
    return n


def listing(cfgdir=None, with_activity=True):
    """Every loop claudectl started, newest first, with live state.

    For a SESSION loop `running` is the process — a session can be open with its
    loop already stopped from inside, so the board says "session open" rather
    than claiming the loop is still scheduled. For a SCHEDULED loop it is the
    scheduler entry, which is the thing that will actually fire again."""
    out = []
    for row in reversed(_load(cfgdir)):
        r = dict(row)
        r.setdefault('kind', 'session')
        r['expired'] = bool(r.get('expires')) and time.time() >= r['expires']
        if r['kind'] == 'schedule':
            r['running'] = bool(is_scheduled(r['id'])) and not r.get('stopped')
            r['unknown_pid'] = False
            r['expires_in'] = max(0, int((r.get('expires') or 0) - time.time()))
        else:
            alive = proc.pid_alive(r.get('pid') or 0)
            r['running'] = bool(alive) and not r.get('stopped')
            r['unknown_pid'] = alive is None
        r['age'] = int(time.time() - (r.get('started') or time.time()))
        r['iterations'] = 0
        r['last_activity'] = 0
        if with_activity and r['running'] and r['kind'] == 'session':
            found = _newest_transcript(r)
            if found:
                jsonl, mtime = found
                r['last_activity'] = int(time.time() - mtime)
                r['transcript'] = os.path.basename(jsonl)[:-6]
                try:
                    r['iterations'] = _turns_since(jsonl, r['started'])
                except Exception:
                    pass
        out.append(r)
    return out


def stop(loop_id, cfgdir=None):
    """Stop a loop for good. (ok, message).

    A SCHEDULED loop is removed from the OS scheduler, which is exact: it cannot
    fire again. A SESSION loop has no such lever — its tasks live inside the
    conversation — so stopping it means ending that session, which is blunt and
    is labelled as such wherever it is offered.
    """
    rows = _load(cfgdir)
    for row in rows:
        if row.get('id') != loop_id:
            continue
        if row.get('kind') == 'schedule':
            ok, msg = unschedule(loop_id)
            row['stopped'] = time.time()
            _save(rows, cfgdir)
            return ok, msg
        pid = int(row.get('pid') or 0)
        if not pid:
            return False, 'claudectl has no process handle for that loop'
        if proc.pid_alive(pid) is False:
            row['stopped'] = time.time()
            _save(rows, cfgdir)
            return True, 'That session had already ended'

        class _P:                      # kill_tree wants a Popen-shaped thing
            def __init__(self, pid):
                self.pid = pid

            def poll(self):
                return None

            def kill(self):
                pass
        proc.kill_tree(_P(pid))
        row['stopped'] = time.time()
        _save(rows, cfgdir)
        return True, 'Session ended'
    return False, 'No such loop'


def forget(loop_id, cfgdir=None):
    """Drop a finished loop from the board. Never touches the session."""
    rows = [r for r in _load(cfgdir) if r.get('id') != loop_id]
    _save(rows, cfgdir)
    return True


def renew(loop_id, cfgdir=None, ttl=DEFAULT_TTL):
    """Push a scheduled loop's expiry out. Explicit, because a loop that renews
    itself is a loop that runs forever by accident."""
    rows = _load(cfgdir)
    for row in rows:
        if row.get('id') == loop_id:
            row['expires'] = time.time() + (ttl or DEFAULT_TTL)
            _save(rows, cfgdir)
            return True, 'Renewed for %d days' % ((ttl or DEFAULT_TTL) // 86400)
    return False, 'No such loop'


# ── the OS scheduler ─────────────────────────────────────────
# One platform branch, in the shape proc.py uses: never raises, returns
# (ok, message). What runs is `claudectl --loop-run <id>`, so everything the
# schedule needs to know stays in the registry rather than in a command line
# that a user might see and would have to be re-created to change.

TASK_PREFIX = 'claudectl-loop-'


def task_name(loop_id):
    return TASK_PREFIX + str(loop_id)


def _interval_minutes(interval):
    """'15m' / '2h' / '1d' → minutes. Anything unparseable is an hour, which is
    the safe direction to be wrong in."""
    m = re.match(r'^\s*(\d+)\s*([smhd]?)\s*$', str(interval or ''))
    if not m:
        return 60
    n, unit = int(m.group(1)), (m.group(2) or 'm')
    mins = {'s': max(1, n // 60), 'm': n, 'h': n * 60, 'd': n * 1440}[unit]
    return max(1, min(mins, 7 * 1440))


def _runner_argv(loop_id, cfgdir):
    """What the scheduler runs. `pythonw` where it exists: a console window
    flashing up every fifteen minutes is how a background feature gets turned
    off."""
    exe = sys.executable or 'python'
    pyw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
    if proc.WINDOWS and os.path.isfile(pyw):
        exe = pyw
    argv = [exe, '-m', 'claude_sessions', '--loop-run', str(loop_id)]
    if cfgdir:
        argv += ['--cfgdir', cfgdir]
    return argv


def _quote_win(argv):
    return ' '.join('"%s"' % a if ' ' in a else a for a in argv)


def schedule(loop_id, interval, cfgdir=None):
    """Register the OS scheduler entry. (ok, message)."""
    mins = _interval_minutes(interval)
    argv = _runner_argv(loop_id, cfgdir)
    if proc.WINDOWS:
        # No /ru: the task runs as the logged-on user, so nothing has to store a
        # password. The cost is that it fires only while that user is logged in,
        # which is the right trade for a developer tool.
        if mins % 1440 == 0:
            sched = ['/sc', 'daily', '/mo', str(mins // 1440)]
        elif mins % 60 == 0:
            sched = ['/sc', 'hourly', '/mo', str(mins // 60)]
        else:
            sched = ['/sc', 'minute', '/mo', str(mins)]
        r = proc.run(['schtasks', '/create', '/tn', task_name(loop_id),
                      '/tr', _quote_win(argv), '/f'] + sched, timeout=30)
        if r is None or r.returncode:
            return False, (getattr(r, 'stderr', '') or 'schtasks failed').strip()[:200]
        return True, 'Scheduled every %d min' % mins
    return _cron_write(loop_id, mins, argv)


def unschedule(loop_id):
    """Remove the entry. Reporting success when it was already gone is correct:
    the caller asked for it not to fire again."""
    if proc.WINDOWS:
        r = proc.run(['schtasks', '/delete', '/tn', task_name(loop_id), '/f'],
                     timeout=30)
        if r is None:
            return False, 'schtasks not available'
        return True, 'Removed from Task Scheduler'
    return _cron_write(loop_id, 0, None)


def is_scheduled(loop_id):
    if proc.WINDOWS:
        r = proc.run(['schtasks', '/query', '/tn', task_name(loop_id)], timeout=30)
        return bool(r is not None and r.returncode == 0)
    r = proc.run(['crontab', '-l'], timeout=15)
    return bool(r and r.returncode == 0 and _cron_tag(loop_id) in (r.stdout or ''))


def _cron_tag(loop_id):
    return '# ' + task_name(loop_id)


def _cron_write(loop_id, mins, argv):
    """Rewrite the user's crontab with this loop's line added or removed.

    Read-modify-write of a file that belongs to the user, so every line that is
    not ours is carried through untouched — the same discipline as settings.json.
    """
    r = proc.run(['crontab', '-l'], timeout=15)
    if r is None:
        return False, 'cron is not available on this machine'
    lines = [ln for ln in (r.stdout or '').splitlines()
             if _cron_tag(loop_id) not in ln]
    if argv is not None:
        spec = ('*/%d * * * *' % mins if mins < 60 else
                ('0 */%d * * *' % (mins // 60) if mins < 1440 else
                 '0 3 */%d * *' % (mins // 1440)))
        lines.append('%s %s %s' % (spec, ' '.join(argv), _cron_tag(loop_id)))
    text = '\n'.join(lines).strip() + '\n'
    w = proc.run(['crontab', '-'], stdin=text, timeout=15)
    if w is None or w.returncode:
        return False, (getattr(w, 'stderr', '') or 'crontab failed').strip()[:200]
    return True, ('Scheduled every %d min' % mins) if argv is not None else 'Removed from cron'


# ── one scheduled run ────────────────────────────────────────

def run_once(loop_id, cfgdir=None):
    """Execute one iteration. This is what the scheduler actually runs.

    Everything that bounds the damage lives HERE rather than in the UI, because
    this is the only code path a forgotten task still reaches: the expiry
    unschedules itself, and the per-run budget cap rides on the invocation.
    """
    rows = _load(cfgdir)
    row = next((r for r in rows if r.get('id') == loop_id), None)
    if row is None:
        unschedule(loop_id)          # a task with no row is a leftover
        return 2
    if row.get('stopped'):
        unschedule(loop_id)
        return 0
    if row.get('expires') and time.time() >= row['expires']:
        unschedule(loop_id)
        row['stopped'] = time.time()
        row['last_error'] = 'expired'
        _save(rows, cfgdir)
        return 0

    started = time.time()
    out, err, code = _run_claude(row)
    result, cost, sid = _parse_result(out)
    row['runs'] = int(row.get('runs') or 0) + 1
    row['last_run'] = time.time()
    row['last_cost'] = cost
    row['last_result'] = (result or '').strip()[:400]
    row['last_error'] = '' if code == 0 else (err or 'exit %s' % code)[:200]
    row['session'] = sid or row.get('session', '')
    entry = {'at': row['last_run'], 'ok': code == 0, 'cost': cost,
             'secs': int(time.time() - started),
             'text': (row['last_error'] or row['last_result'] or '(no output)')[:300]}
    row['journal'] = ([entry] + list(row.get('journal') or []))[:MAX_JOURNAL]
    _save(rows, cfgdir)

    try:
        write_journal_block(row)
    except Exception:
        pass
    if code != 0:
        try:
            from . import notify
            notify.send('Loop failed — %s' % row['name'],
                        row['last_error'] or 'See the loops board.')
        except Exception:
            pass
    return 0 if code == 0 else 1


def _run_claude(row):
    """(stdout, stderr, returncode) for one headless iteration.

    NOT `--bare`: that mode skips hooks, skills, CLAUDE.md, memory and MCP — in
    other words everything claudectl provisions — and it refuses the
    subscription login. The whole point of running the loop through claudectl is
    that it arrives with the project's context.
    """
    from .config import get_claude_exe, account_env
    from .memory import _budget_args
    exe = get_claude_exe()
    if not exe:
        return '', 'claude.exe not found', 127
    args = [exe, '-p', '--output-format', 'json',
            '--permission-mode', row.get('perm') or 'auto']
    args += _budget_args()
    prompt = row.get('prompt') or _default_prompt(row)
    # A loop fires unattended, so the guard reports rather than asks; the reason
    # lands in row['last_error'], the journal and the notification for free.
    from . import quota
    env, blocked = quota.preflight(args, account_env(row.get('cfgdir') or None))
    if blocked:
        return '', blocked, 1
    r = proc.run(args, cwd=row.get('path') or None, env=env,
                 stdin=prompt, timeout=3600)
    if r is None:
        return '', 'could not start claude', 127
    return r.stdout or '', r.stderr or '', r.returncode


def _default_prompt(row):
    """A loop with no prompt runs the project's loop.md, falling back to the
    account one — the same precedence `/loop` itself applies. Read here rather
    than baked into the schedule so an edit takes effect on the next run."""
    for p in (os.path.join(row.get('path') or '', '.claude', 'loop.md'),
              os.path.join(row.get('cfgdir') or _c.config_dir, 'loop.md')):
        try:
            text = open(p, encoding='utf-8', errors='ignore').read().strip()
        except OSError:
            continue
        if text:
            return text[:25000]      # Claude Code truncates loop.md at 25KB
    return ('Continue the unfinished work in this project. If there is nothing '
            'to do, say so in one line and stop.')


def _parse_result(out):
    """(result_text, cost_usd, session_id) from `--output-format json`.

    Tolerant on purpose: a run that produced something unparseable still counts
    as a run, and the raw text is better than a blank row."""
    try:
        d = json.loads(out or '{}')
    except ValueError:
        return (out or '').strip()[:400], 0.0, ''
    if not isinstance(d, dict):
        return (out or '').strip()[:400], 0.0, ''
    return (str(d.get('result') or '').strip(),
            float(d.get('total_cost_usd') or 0.0),
            str(d.get('session_id') or ''))


def write_journal_block(row):
    """Rewrite the CLAUDECTL:LOOP block in the project's CLAUDE.md.

    REWRITTEN, never appended: this text is read on every turn of every session
    in the project, so an append-only log would quietly become the most
    expensive thing in the file. The last few outcomes are what a fresh run
    needs to avoid repeating itself, and older ones are dead weight.
    """
    from .claude_md import upsert_block
    from .config import _LOOP_START, _LOOP_END
    path = row.get('path') or ''
    if not path or not os.path.isdir(path):
        return False
    entries = list(row.get('journal') or [])[:JOURNAL_IN_MD]
    if not entries:
        upsert_block(path, _LOOP_START, _LOOP_END, '')
        return True
    lines = []
    for e in entries:
        when = time.strftime('%Y-%m-%d %H:%M', time.localtime(e.get('at') or 0))
        mark = '' if e.get('ok') else 'FAILED — '
        lines.append('- %s — %s%s' % (when, mark, ' '.join(str(e.get('text') or '').split())[:220]))
    section = (
        f"{_LOOP_START}\n## Background loop — recent runs (claudectl — auto-maintained)\n"
        "%s\n\n"
        "This project has a scheduled loop (`%s`). What it did, newest first — "
        "continue from here rather than starting over:\n\n%s\n%s\n"
        % (_c.generated_note("this project's background loop, after every run "
                             "(the last %d are kept)" % JOURNAL_IN_MD,
                             'the Loops page'),
           row.get('text') or 'loop', '\n'.join(lines), _LOOP_END))
    upsert_block(path, _LOOP_START, _LOOP_END, section)
    return True
