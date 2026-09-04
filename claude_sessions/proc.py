"""Every subprocess and process-lifetime primitive, in one place.

There were three git wrappers (two identical with their arguments in the
opposite order), two byte-for-byte copies of `_pid_alive`, two copies of the
`taskkill /T` tree-kill, and four hand-rolled terminal spawns. That is four to
eight places to write each platform branch, which is why this module has to
exist before the POSIX port rather than after it.
"""

import os
import re as _re
import subprocess
import sys
import time

__all__ = ['run', 'git', 'pid_alive', 'kill_tree', 'spawn_terminal',
           'wait_and_run', 'new_console_flags', 'no_window_flags', 'WINDOWS']

WINDOWS = os.name == 'nt'

#: CREATE_NEW_CONSOLE where it exists, 0 elsewhere — `getattr` because the
#: constant is not defined on POSIX and a bare reference is an AttributeError
#: at import, not at the call.
new_console_flags = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)

#: CREATE_NO_WINDOW — the opposite flag, and the one every CAPTURED call needs.
#: Without it Windows gives each console child its own console window: opening
#: the Repos or Tools tab runs git across a dozen repos and the user watches a
#: dozen black windows flash open and shut. Nothing is ever shown in them —
#: stdout and stderr are captured — so the window is pure visual noise.
no_window_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run(args, *, cwd=None, env=None, timeout=30, stdin=None, check=False):
    """`subprocess.run` with the decoding pinned and the console suppressed.
    Returns the CompletedProcess, or None if it could not be run at all.

    `text=True` alone decodes with the locale codepage — cp1252 on Windows —
    so one non-ASCII path or branch name raises *inside* subprocess and the
    caller concludes the command failed. Every call in this codebase goes
    through here for that reason.

    `creationflags` is the second reason: output is captured, so a console
    window would show nothing and only flash. See no_window_flags.

    With no `stdin`, the child gets DEVNULL rather than inheriting ours. A CLI
    that decides to ask a question would otherwise block forever on a terminal
    nobody is watching — and inside a captured run there is no prompt to see.
    """
    try:
        r = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                           text=True, encoding='utf-8', errors='ignore',
                           timeout=timeout, input=stdin,
                           stdin=None if stdin is not None else subprocess.DEVNULL,
                           creationflags=no_window_flags)
    except Exception:
        return None
    if check and r.returncode:
        return None
    return r


def git(args, cwd, timeout=15):
    """git stdout, or None when the command failed or git is absent."""
    r = run(['git'] + list(args), cwd=cwd, timeout=timeout)
    if r is None or r.returncode:
        return None
    return r.stdout


#: schemes a remote may use. Deliberately an allowlist, not a denylist of the
#: dangerous ones — git keeps adding transports.
_REMOTE_RE = _re.compile(r'^(?:https?://|ssh://|git://|git@[\w.-]+:)[\w.@:/~%-]')


def remote_url_ok(url):
    """Is this safe to hand to `git clone` / `claude plugin marketplace add`?

    Two separate hazards, and argv-list form only closes one of them:

      * `ext::sh -c <payload>` is a real git transport, and `protocol.ext.allow`
        defaults to `user` — which a direct CLI invocation is. Cloning that URL
        executes the payload. No shell is involved; git IS the shell.
      * a URL beginning `-` lands in an OPTION position (`--upload-pack=…`,
        `--config=…`), so callers must also pass `--` before it.

    An allowlist of schemes is the whole fix. Callers still add `--`.
    """
    u = str(url or '').strip()
    return bool(u) and len(u) < 2048 and bool(_REMOTE_RE.match(u))


def pid_alive(pid):
    """True / False, or None when it cannot be determined.

    NEVER `os.kill(pid, 0)` on Windows — that signal number is not a probe
    there, it terminates the process.
    """
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                # Opening a handle is not enough: an exited process keeps one
                # until every handle is closed, so ask for the exit code.
                code = ctypes.c_ulong()
                if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == 259          # STILL_ACTIVE
                return False
            finally:
                k32.CloseHandle(h)
        except Exception:
            return None                               # unknown → age decides
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except Exception:
        return None


def wait_and_run(pid, argv, timeout=300, poll=0.5, out=print):
    """Wait for *pid* to exit, then run *argv* and return its exit code.

    This is what lets a program replace its own files: claudectl's upgrade
    rewrites the console script it is running from, which Windows keeps locked
    until the process ends. Lives here rather than in versions.py so the waiting
    process can reach it without importing the package pip is replacing — this
    module imports only the standard library, nothing from claudectl.

    `pid_alive` returning None means "cannot tell"; that stops the wait rather
    than hanging on it, and the command's own error is then the honest report.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        pid = 0
    if not argv:
        return 2
    out('Waiting for claudectl to exit...')
    deadline = time.time() + timeout
    while pid and time.time() < deadline:
        if pid_alive(pid) is not True:
            break
        time.sleep(poll)
    out('Running: ' + ' '.join(argv))
    try:
        return subprocess.call(argv)
    except Exception as e:
        out('Failed: %s' % e)
        return 1


def kill_tree(proc):
    """Kill a process and everything it spawned. `Popen.kill` on Windows kills
    only the direct child, which for a `cmd /c claude ...` chain leaves the
    thing the user actually wanted stopped still running."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if WINDOWS:
            r = subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                               capture_output=True,
                               creationflags=no_window_flags)
            if r.returncode:      # no such pid, or access denied — try direct
                proc.kill()
        elif os.getpgid(proc.pid) == proc.pid:
            # Only when the child leads its own group. Otherwise its group is
            # OURS, and killpg would take down claudectl along with it.
            os.killpg(proc.pid, 15)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── terminal windows ─────────────────────────────────────────

#: tried in order; the first one on PATH wins
_POSIX_TERMINALS = [
    ('x-terminal-emulator', ['-e']),
    ('gnome-terminal', ['--']),
    ('konsole', ['-e']),
    ('xfce4-terminal', ['-e']),
    ('alacritty', ['-e']),
    ('kitty', []),
    ('xterm', ['-e']),
]


def spawn_terminal(argv=None, *, cwd=None, env=None, title='', keep_open=False):
    """Open a NEW terminal window running *argv* (a plain shell when None).

    Returns (Popen|None, error). *keep_open* leaves the window up after the
    command exits — the "just give me a shell here" case. Otherwise the window
    closes on ANY exit code: ending a Claude session with Ctrl+C returns
    non-zero on Windows, and a `|| pause` left the window stuck on a keypress
    for every normal exit, not just crashes.
    """
    if WINDOWS:
        return _spawn_windows(argv, cwd, env, title, keep_open)
    return _spawn_posix(argv, cwd, env, title, keep_open)


def _spawn_windows(argv, cwd, env, title, keep_open):
    # argv-list form, never shell=True: list2cmdline quotes each argument, so a
    # project or account name containing " & | cannot break out of the title
    # and run something else.
    if argv is None:
        cmd = ['cmd', '/k'] + (['title', title] if title else [])
    elif keep_open:
        cmd = ['cmd', '/k'] + (['title', title, '&&'] if title else []) + list(argv)
    else:
        cmd = ['cmd', '/c'] + (['title', title, '&&'] if title else []) + list(argv)
    try:
        return subprocess.Popen(cmd, cwd=cwd, env=env,
                                creationflags=new_console_flags), ''
    except Exception as e:
        return None, str(e)


def _spawn_posix(argv, cwd, env, title, keep_open):
    import shutil

    inner = list(argv) if argv else [os.environ.get('SHELL') or '/bin/sh']
    if keep_open and argv:
        sh = os.environ.get('SHELL') or '/bin/sh'
        inner = [sh, '-c', _quote(inner) + '; exec ' + sh]

    if sys.platform == 'darwin':
        script = 'cd %s && %s' % (_quote([cwd or os.getcwd()]), _quote(inner))
        try:
            return subprocess.Popen(
                ['osascript', '-e',
                 'tell application "Terminal" to do script %s' % _applescript_str(script)],
                env=env), ''
        except Exception as e:
            return None, str(e)

    for name, flag in [(os.environ.get('TERMINAL') or '', ['-e'])] + _POSIX_TERMINALS:
        if not name or not shutil.which(name):
            continue
        try:
            return subprocess.Popen([name] + flag + inner, cwd=cwd, env=env,
                                    start_new_session=True), ''
        except Exception:
            continue
    # No terminal emulator at all (a headless box, or a container). Detaching is
    # still better than refusing: the caller reports the command so the user can
    # run it in their own window.
    try:
        return subprocess.Popen(inner, cwd=cwd, env=env, start_new_session=True), ''
    except Exception as e:
        return None, str(e)


def _quote(args):
    import shlex
    return ' '.join(shlex.quote(a) for a in args)


def _applescript_str(s):
    return '"%s"' % s.replace('\\', '\\\\').replace('"', '\\"')
