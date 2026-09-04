import os
import threading
import time

from .config import W, get_claude_exe, open_in_editor
from .sessions import get_session_info
from .ui import (menu, _cls, pause, run_with_progress, text_input,
                 confirm, flash, paths_menu, pager)
from . import config as _c
from . import render


# ── MCP status ────────────────────────────────────────────────

#: `claude mcp list` connects to every configured server before answering, so it
#: costs ~1.7s measured — 42% of a profiled /api/dashboard, which the SPA polls
#: every 10 seconds. The endpoint's own `_dash_cache` cannot help: its TTL and
#: the poll interval are the same number, so every poll was a cache miss and a
#: fresh subprocess. A server going up or down is not something you need to see
#: inside 30s, and every MCP page action already busts this by calling with
#: `refresh=True`.
#: ponytail: process-local dict, no lock — a duplicate probe is harmless
_STATUS_TTL = 30
_status_cache = {}


def get_mcp_status(cfgdir=None, refresh=False):
    """Run 'claude mcp list', return list of (name, status) tuples. Cached 30s.

    cfgdir names the account: `claude mcp` honours CLAUDE_CONFIG_DIR, and
    without it the list came from whichever account claudectl inherited while
    every other MCP surface resolved the active one."""
    key = _c.resolve_config_dir(cfgdir)
    hit = _status_cache.get(key)
    if hit and not refresh and time.time() - hit[0] < _STATUS_TTL:
        return list(hit[1])

    def miss():
        """Cache the empty answer too.

        Only the success path wrote the cache, so the three ways this returns []
        — no claude.exe, proc.run gave up, the parse raised — re-spawned a 1.7s
        subprocess on every 10-second dashboard poll. That is the case that
        costs the MOST, because `claude mcp list` returning nothing usually
        means it is slow-failing or timing out. Same argument `paths.py` makes
        for caching a negative result.
        """
        _status_cache[key] = (time.time(), [])
        return []

    claude_exe = get_claude_exe()
    if not claude_exe:
        return miss()
    # Through proc.run, not subprocess directly. It was the last hand-rolled
    # spawn outside that module, and being outside it had a cost: the test
    # suite's process stubs patch `proc`, so every endpoint test that touched
    # MCP ran the REAL `claude mcp list` against the user's real account —
    # caught by the conftest guard that restores files a test damaged.
    from . import proc
    try:
        r = proc.run([claude_exe, 'mcp', 'list'], timeout=10,
                     env=_c.account_env(cfgdir))
        if r is None:
            return miss()
        lines = (r.stdout + r.stderr).splitlines()
        servers = []
        for line in lines:
            line = line.strip()
            if not line or line.lower().startswith('checking'):
                continue
            if ':' not in line:
                continue
            name = line.split(':')[0].strip().replace('claude.ai ', '')
            if '✔' in line or 'Connected' in line:
                servers.append((name, 'ok'))
            elif '!' in line or 'auth' in line.lower():
                servers.append((name, 'auth'))
            else:
                # A failed/timed-out server used to match neither branch and
                # vanish from the list entirely — the one state the user most
                # needs to see.
                servers.append((name, 'fail'))
        _status_cache[key] = (time.time(), list(servers))
        return servers
    except Exception:
        return miss()


def _status_icon(status):
    if status == 'ok':
        return f'{_c.C_OK}✔{_c.C_RESET}'
    if status == 'fail':
        return f'{_c.C_ERR}✘{_c.C_RESET}'
    return f'{_c.C_WARN}!{_c.C_RESET}'


mcp_servers = []
_mcp_ready = False
_mcp_error = False

def _mcp_background():
    global mcp_servers, _mcp_ready, _mcp_error
    try:
        mcp_servers = get_mcp_status()
    except Exception:
        _mcp_error = True
        _c.log.exception('mcp status failed')
    _mcp_ready = True

threading.Thread(target=_mcp_background, daemon=True).start()


# ── global CLAUDE.md / MCP analysis ──────────────────────────

def analyze_mcp_tools(mcp_name):
    """Run claude --print to get MCP tool list. Shows progress. Returns markdown string."""
    claude_exe = get_claude_exe()
    if not claude_exe:
        return ''
    prompt = (
        f"Using the {mcp_name} MCP server, call the tools/list endpoint and list every available tool. "
        f"For each tool output: tool name, one-line description, and key parameters. "
        f"Format as markdown. Be concise. No intro text. "
        f"Do not create, write, or edit any files — output the markdown directly."
    )
    # prompt BEFORE --disallowedTools (variadic flag would swallow it)
    from .memory import extract_model
    _mf = ['--model', extract_model()] if extract_model() else []
    out, cancelled = run_with_progress(
        [claude_exe, *_mf, '--print', prompt,
         '--disallowedTools', 'Write,Edit,NotebookEdit,Bash'],
        ('CLAUDECTL', mcp_name, 'MCP ANALYSIS'),
        f'Analyzing {mcp_name} MCP tools via Claude...  (15-60s)',
        timeout=120)
    if cancelled:
        return ''
    return (out or '').strip()


#: what an empty global CLAUDE.md starts as, so the TUI and the GUI create the
#: same file rather than two slightly different ones.
GLOBAL_MD_STUB = ('# Global Claude Context\n'
                  '<!-- This file is read by Claude in every session -->\n\n')


def update_global_claude_md_mcp(mcp_name, tools_doc, cfgdir=None):
    """Write/update MCP section in global CLAUDE.md using per-MCP sentinels.

    Atomic, because Claude Code reads this file every session: a plain
    open(...,'w') that dies partway leaves a half-written file and breaks the
    user's whole session, not just claudectl. The existing gate only walks
    writers NAMED settings, so it never looked here.
    """
    path      = _c.global_claude_md_for(cfgdir)
    start_tag = f'<!-- MCP:{mcp_name}:START -->'
    end_tag   = f'<!-- MCP:{mcp_name}:END -->'
    section   = f"{start_tag}\n## MCP: {mcp_name}\n{tools_doc}\n{end_tag}\n"

    existing = ''
    if os.path.exists(path):
        try:
            existing = open(path, encoding='utf-8', errors='ignore').read()
        except Exception:
            pass

    if start_tag in existing and end_tag in existing:
        pre  = existing[:existing.index(start_tag)]
        post = existing[existing.index(end_tag) + len(end_tag):]
        final = pre + section + post
    elif existing:
        final = existing.rstrip('\n') + '\n\n' + section
    else:
        final = '# Global Claude Context\n<!-- Edit freely — MCP sections auto-updated -->\n\n' + section

    return _c.write_atomic(path, final)


def global_claude_md_menu():
    """Sub-menu: pick MCP to analyze, or edit global CLAUDE.md."""
    from . import config as _c
    global_claude_md = _c.global_claude_md_for()
    mcp_items = []
    for name, status in mcp_servers:
        icon = _status_icon(status)
        mcp_items.append((f"{icon}  {name}", f'mcp:{name}'))
    mcp_items += [(f"{'─' * W}", None), ('📝  Edit global CLAUDE.md in editor', '__edit__')]

    while True:
        sel = menu(mcp_items, "GLOBAL CLAUDE.md  /  Select MCP to analyze")
        if not sel:
            return
        if sel == '__edit__':
            if not os.path.exists(global_claude_md):
                _c.write_atomic(global_claude_md, GLOBAL_MD_STUB)
            open_in_editor(global_claude_md)
            return
        if sel.startswith('mcp:'):
            mcp_name = sel[4:]
            tools_doc = analyze_mcp_tools(mcp_name)
            if tools_doc:
                ok = update_global_claude_md_mcp(mcp_name, tools_doc)
                _cls()
                if ok:
                    print(f"\n  ✔ Written to {global_claude_md}\n")
                    print(f"  Claude will see {mcp_name} tool docs in every session.\n")
                    open_in_editor(global_claude_md)
                else:
                    print(f"\n  ✘ Failed to write {global_claude_md}\n")
            else:
                _cls()
                print(f"\n  ✘ No output from Claude — MCP may need authentication.\n")
            pause("  Press Enter to continue...")
            return


# ── MCP server management (claude mcp add/remove/get/list) ───

MCP_SCOPES     = ['local', 'user', 'project']
MCP_TRANSPORTS = ['stdio', 'http', 'sse']


def mcp_cli(args, cfgdir=None, timeout=60):
    """`claude mcp <args>` against ONE account, no TUI. (ok, output).

    The GUI's counterpart to _mcp_run — same account addressing, without the
    progress screen a request thread cannot draw.
    """
    from . import proc
    exe = get_claude_exe()
    if not exe:
        return False, 'claude.exe not found'
    r = proc.run([exe, 'mcp', *args], env=_c.account_env(cfgdir), timeout=timeout)
    if r is None:
        return False, 'could not run claude'
    return r.returncode == 0, ((r.stdout or '') + (r.stderr or '')).strip()


def _mcp_run(args, label, crumbs=('CLAUDECTL', 'MCP'), cfgdir=None):
    """Run `claude mcp <args>` with progress. Returns (stdout, cancelled)."""
    claude = get_claude_exe()
    if not claude:
        return None, False
    return run_with_progress([claude, 'mcp', *args], crumbs, label, timeout=60,
                             env=_c.account_env(cfgdir))


def _list_servers(cfgdir=None):
    """Parsed server rows: [(name, status, raw_line)]."""
    rows = []
    for name, status in get_mcp_status(cfgdir):
        rows.append((name, status))
    return rows


def mcp_manager_menu():
    """Full MCP management: list / add / remove / detail via `claude mcp`."""
    if not get_claude_exe():
        _cls()
        print("\n  claude.exe not found — cannot manage MCP servers.\n")
        pause("  Press Enter...")
        return

    while True:
        servers = _list_servers()
        items = []
        for name, status in servers:
            icon = _status_icon(status)
            items.append((f"{icon}  {name}", f'srv:{name}'))
        if not servers:
            items.append((f"{_c.C_DIM}(no MCP servers configured){_c.C_RESET}", None))
        items += [(f"{'─' * W}", None),
                  ('＋  Add MCP server', '__add__'),
                  ('↻  Re-check status', '__refresh__'),
                  ('📝  Global CLAUDE.md / tool docs', '__docs__')]

        sel = menu(items, "MCP SERVERS")
        if not sel:
            return
        if sel == '__refresh__':
            global mcp_servers, _mcp_ready
            mcp_servers = get_mcp_status()
            _mcp_ready = True
            flash("Status refreshed")
        elif sel == '__docs__':
            global_claude_md_menu()
        elif sel == '__add__':
            _mcp_add_flow()
        elif sel.startswith('srv:'):
            _mcp_detail(sel[4:])


def _mcp_add_flow():
    name = text_input("MCP server name:")
    if not name:
        return
    transport = menu([(t, t) for t in MCP_TRANSPORTS], "TRANSPORT")
    if transport is None:
        return
    if transport == 'stdio':
        target = text_input("Command to run (e.g. npx my-mcp-server):")
    else:
        target = text_input(f"Server URL ({transport}):")
    if not target:
        return
    scope = menu([(s, s) for s in MCP_SCOPES], "SCOPE")
    if scope is None:
        return

    args = ['add', '-s', scope, '-t', transport, name]
    if transport == 'stdio':
        # split command into the bare program + args after `--`
        parts = target.split()
        args += ['--', *parts]
    else:
        args += [target]

    out, cancelled = _mcp_add_with_extras(args, transport, name)
    if cancelled:
        flash("Cancelled", ok=False)
        return
    flash(f"Added {name}" if out is not None else "Add failed",
          ok=out is not None, secs=1.4)


def _mcp_add_with_extras(args, transport, name):
    # optional env vars / headers via line-list temp prompts
    extra = []
    if transport == 'stdio':
        env = text_input("Env vars KEY=VAL (space-separated, blank = none):")
        for kv in env.split():
            extra += ['-e', kv]
    else:
        hdr = text_input("Header 'Name: value' (blank = none):")
        if hdr:
            extra += ['-H', hdr]
    # insert extras before the trailing target/command
    final = args[:3] + extra + args[3:]
    return _mcp_run(final, f"Adding MCP server {name}...")


def _mcp_detail(name):
    out, _ = _mcp_run(['get', name], f"Loading {name}...")
    lines = (out or 'no details').splitlines() or ['(empty)']
    while True:
        key = pager(('CLAUDECTL', 'MCP', name), lines,
                    hint="d remove   t tool docs", extra_keys=('d', 't'))
        if key == 'd':
            scope = menu([(s, s) for s in MCP_SCOPES], f"REMOVE {name} — scope")
            if scope and confirm(f"Remove MCP server '{name}' ({scope})?", danger=True):
                res, _ = _mcp_run(['remove', name, '-s', scope], f"Removing {name}...")
                flash(f"Removed {name}", secs=1.2)
                return
        elif key == 't':
            doc = analyze_mcp_tools(name)
            if doc:
                pager(('CLAUDECTL', 'MCP', name, 'TOOLS'), doc.splitlines())
            else:
                flash("No tool docs (MCP may need auth)", ok=False, secs=1.4)
        else:
            return


def mcp_status_line():
    if _mcp_error:
        return f'  {_c.C_WARN}MCP: unavailable{_c.C_RESET}'
    if not _mcp_ready:
        return f'  {_c.C_DIM}MCP: checking...{_c.C_RESET}'
    connected = [name for name, status in mcp_servers if status == 'ok']
    if not connected:
        return ''
    servers = '   '.join(f'{_c.C_OK}✔{_c.C_RESET} {n}' for n in connected)
    return f'  {_c.C_DIM}MCP:{_c.C_RESET} {servers}'
