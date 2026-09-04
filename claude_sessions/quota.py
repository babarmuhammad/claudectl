"""Don't spend an account that has nothing left — offer one that does.

claudectl makes its own headless `claude -p` calls for every generate-this-for-me
feature (AI agents, skills, MCP analysis, system prompts, CLAUDE.md, memory and
lessons extraction, Plan→Execute and its council, scheduled loops). None of them
used to ask whether the account they were about to spend had any quota left, so
a full 5-hour window produced a nonzero exit reported as "No output from Claude"
— while a second configured account sat there with headroom.

Three things make this cheap enough to sit in front of every call:

* **It reads only what `usage.py` already polled.** No network, ever. The poller
  refreshes every 300s on a daemon thread; this module reads its cache and
  nothing else.
* **It fails open.** No entry, a stale poll, an account that has never been
  polled — all pass through. A guard that blocks work because a background
  thread has not run yet is worse than the bug it fixes.
* **It only looks at `claude … -p`.** `claude mcp list`, `claude plugin …` and
  `claude --version` share the same spawn helpers and consume no quota; blocking
  them the moment a weekly cap filled would break account management outright.

`note_failure()` closes the one hole the cache leaves: on a cold cache the first
call of a burst still gets through, and its error text latches the account so the
rest of the burst skips with the real reason instead of repeating the failure.
"""

import os
import re
import time

from . import config as _c

LIMIT_PCT = 100.0

#: Account-wide windows only. `usage._limit_label` returns the MODEL's display
#: name for a `weekly_scoped` limit, and refusing a haiku extraction because the
#: Opus weekly window is spent would be a worse bug than the one being fixed.
ACCOUNT_WINDOWS = ('session', 'weekly')

#: one answer covers a burst — a 30-unit memory refresh asks once, not 30 times.
#: A guard that prompts per call is worse than the bug.
_DECIDED_TTL = 900
#: how long a limit error we actually SAW is trusted, independent of the poller
_OBSERVED_TTL = 900

_observed = {}     # normalised cfgdir -> until_ts
_decided = {}      # normalised cfgdir -> (choice, until_ts)

#: substrings that mean "out of quota". Deliberately specific: a bare 'limit'
#: also matches the budget cap and the turn cap, neither of which is this.
#:
#: 'session limit' and 'weekly limit' are the two Claude Code actually sends, and
#: their absence made this whole module a no-op for the common case. The real
#: refusal reads "You've hit your session limit · resets 2:30am (Europe/Rome)" —
#: which matched NONE of the markers above it, so `note_failure` never latched
#: and the mechanism its own docstring describes ("that one failure is what stops
#: the other five") had never once fired. The event log showed the consequence:
#: four spawned-and-refused calls in 19 minutes against an account that was
#: already saying no. Checked against a real 429 transcript, not guessed — the
#: same discipline as reading `context_window.used_percentage` off the payload.
_LIMIT_MARKERS = ('usage limit', 'rate limit', 'rate_limit', 'ratelimit',
                  'rate-limited', 'limit reached', 'limit exceeded',
                  'session limit', 'weekly limit',
                  'out of quota', 'quota exceeded', 'too many requests')

#: `429` needs a word boundary, and `quota` needed qualifying. `ui._note_failure`
#: now hands the model's whole STDOUT to `note_failure`, not a 300-char stderr
#: latch — so a bare substring scan matched `14290` in a token count and the word
#: "quota" in any answer that discussed rate limits, and latched the account out
#: of headless work for fifteen minutes on a successful-looking run.
_LIMIT_RE = re.compile(r'429')


def _norm(d):
    return os.path.normcase(os.path.abspath(d or ''))


def _key(cfgdir=None):
    return _norm(_c.resolve_config_dir(cfgdir))


def _cfgdir_of(env):
    """The account a prepared environment points at, or None for the active
    one. `config.account_env` is the only thing that sets this key."""
    if isinstance(env, dict):
        return env.get('CLAUDE_CONFIG_DIR') or None
    return None


def is_inference(args):
    """True only for `claude … -p/--print`, i.e. a call that spends quota.

    Checked before anything is imported, so a `git` call through a shared
    runner costs one basename() and never drags `usage` (and with it urllib and
    ssl) into a caller that must stay import-light.
    """
    # ponytail: argv sniff, not a typed call — revisit if a management
    # subcommand ever grows -p
    try:
        first = str(args[0] or '')
    except (IndexError, TypeError, KeyError):
        return False
    if not os.path.basename(first).lower().startswith('claude'):
        return False
    return any(a in ('-p', '--print') for a in list(args)[1:])


def worst_window(cfgdir=None):
    """(pct, label, resets_iso) for the fullest account-wide window, from the
    usage poller's cache. (0.0, '', '') means "not known" — which is a pass."""
    try:
        from . import usage
    except Exception:
        return (0.0, '', '')
    want = _key(cfgdir)
    st = None
    try:
        with usage._lock:
            # _acct_state is keyed by the RAW string usage._targets() built, not
            # by a normalised path — an exact lookup here fails open forever and
            # nothing else would ever notice.
            for k, v in usage._acct_state.items():
                if _norm(k) == want:
                    st = dict(v)
                    break
    except Exception:
        return (0.0, '', '')
    if not st or st.get('status') != 'ok' or not st.get('data'):
        return (0.0, '', '')
    worst = (0.0, '', '')
    try:
        for label, pct, reset in usage._extract_windows(st['data']):
            if label in ACCOUNT_WINDOWS and pct > worst[0]:
                worst = (pct, label, reset or '')
    except Exception:
        return (0.0, '', '')
    return worst


def is_exhausted(cfgdir=None):
    return bool(reason(cfgdir))


def reason(cfgdir=None):
    """Why this account cannot be spent, or '' when it can (or when we don't
    know, which is the same answer)."""
    if _observed.get(_key(cfgdir), 0) > time.time():
        return 'account rate-limited by Claude'
    pct, label, reset = worst_window(cfgdir)
    if pct < LIMIT_PCT:
        return ''
    when = ''
    if reset:
        try:
            from . import usage
            when = ' (resets %s)' % usage._fmt_reset(reset)
        except Exception:
            when = ''
    return '%s limit full%s' % (label or 'account', when)


def headroom(exclude=None):
    """[(name, cfgdir, pct)] for accounts worth switching to, emptiest first.

    An account with no usable local credentials is dropped without a request:
    `usage._read_token` / `_token_expired` are plain file reads.
    """
    ex = _key(exclude) if exclude is not None else None
    out = []
    try:
        from . import usage
        for name, d in _c.all_config_dirs():
            if ex is not None and _norm(d) == ex:
                continue
            if reason(d):
                continue
            if not usage._read_token(d) or usage._token_expired(d):
                continue
            out.append((name, d, worst_window(d)[0]))
    except Exception:
        return []
    out.sort(key=lambda r: r[2])
    return out


def is_limit_error(text):
    t = str(text or '').lower()
    return any(m in t for m in _LIMIT_MARKERS) or bool(_LIMIT_RE.search(t))


def note_failure(env, text):
    """Remember that this account answered with a limit error.

    The poller runs at 300s, so on a cold cache the first call of a burst still
    gets through. That one failure is what stops the other five.
    """
    if not is_limit_error(text):
        return
    _observed[_key(_cfgdir_of(env))] = time.time() + _OBSERVED_TTL


def forget(cfgdir=None):
    """Drop the cached decision and observation for an account (tests, and the
    settings screen after the user changes the policy)."""
    k = _key(cfgdir)
    _observed.pop(k, None)
    _decided.pop(k, None)


def preflight(args, env=None):
    """(env_to_use, blocked_reason) for a subprocess about to be spawned.

    A non-empty blocked_reason means: do not spawn. The returned env may point
    at a DIFFERENT account than the one passed in — that is the whole feature.
    """
    if not is_inference(args):
        return env, ''
    try:
        mode = (_c.load_settings().get('headless_quota') or 'prompt').strip()
    except Exception:
        mode = 'prompt'
    if mode == 'off':
        return env, ''
    cfgdir = _cfgdir_of(env)
    why = reason(cfgdir)
    if not why:
        return env, ''
    key = _key(cfgdir)
    dec = _decided.get(key)
    if dec and dec[1] > time.time():
        return _apply(dec[0], env, why)
    choice = _ask(why, headroom(exclude=cfgdir), mode)
    _decided[key] = (choice, time.time() + _DECIDED_TTL)
    return _apply(choice, env, why)


def _apply(choice, env, why):
    if choice is None:                       # blocked
        _report(why)
        return env, why
    if choice == '':                         # "run anyway"
        return env, ''
    return _c.account_env(choice), ''


def _ask(why, alts, mode):
    """cfgdir to switch to | '' to run anyway | None to block."""
    if mode == 'auto':
        return alts[0][1] if alts else None
    if _interactive():
        return _ask_tui(why, alts)
    job = _job()
    if job is not None and alts:
        return _ask_gui(job, why, alts)
    # Unattended: never prompt, and never quietly drain a second account on a
    # timer. That is exactly the failure the scheduler's own comment describes.
    return None


def _interactive():
    """True only on the TUI's own main thread with a real terminal.

    All three conditions matter. `ui.menu` is NOT bridged by
    `gui_api._install_bridge`, so reaching it from a job thread is a hang rather
    than an error — and `plan_execute._headless` calls `_run_cancellable`
    directly even in the foreground, so the test has to be positive about being
    on the TUI rather than merely "not silent".
    """
    try:
        import sys
        import threading
        from . import memory
        if getattr(memory._tls, 'silent', False):
            return False
        if threading.current_thread() is not threading.main_thread():
            return False
        return bool(getattr(sys.stdin, 'isatty', lambda: False)())
    except Exception:
        return False


def _ask_tui(why, alts):
    from .ui import menu
    items = [('%s   %.0f%% used' % (name, pct), d) for name, d, pct in alts]
    # '__cancel__', never None: a None value is a non-selectable separator.
    items.append(('Run under the current account anyway', '__go__'))
    items.append(('Cancel', '__cancel__'))
    sel = menu(items, '%s — RUN UNDER ANOTHER ACCOUNT?' % why.upper())
    if sel == '__go__':
        return ''
    if not sel or sel == '__cancel__':
        return None
    return sel


def _ask_gui(job, why, alts):
    """Reuse the job approval gate rather than inventing a modal: it already
    renders a title, a list of lines and Approve/Reject, and the browser side
    needs no change at all."""
    from .gui_api import _gate
    name, d, _pct = alts[0]
    lines = ['%s.' % why, '',
             'Accounts with headroom:']
    lines += ['  %s   %.0f%% used' % (n, p) for n, _dd, p in alts]
    lines += ['', "Approve to run this under '%s' instead." % name,
              'Reject to stop and leave the quota alone.']
    # gate['diff'] must be a LIST at every producer — a string is truthy, so the
    # browser's `||[]` fallback never fires and .map() throws.
    ok = _gate(job, "Account limit reached — run under '%s'?" % name,
               '', '', lines)
    return d if ok else None


def _report(why):
    """Put the reason everywhere a caller might read it, instead of letting it
    surface as "No output from Claude"."""
    try:
        from . import memory
        memory.last_call_error = why
    except Exception:
        pass
    job = _job()
    if job is not None:
        try:
            job['last_subprocess_error'] = {'code': 0, 'output': why}
            msgs = job.setdefault('messages', [])
            if not msgs or msgs[-1].get('text') != why:
                msgs.append({'ok': False, 'text': why})
        except Exception:
            pass
    try:
        from . import events
        events.record('quota', why, level='warn',
                      detail='claudectl did not start a Claude call it wanted '
                             'to make')
    except Exception:
        pass


def _job():
    try:
        from . import gui_api
        return getattr(gui_api._JOBCTX, 'job', None)
    except Exception:
        return None
