import os
import subprocess
import threading
import time

from .config import W, global_claude_md, get_claude_exe, open_in_editor
from .sessions import get_session_info
from .ui import (menu, _cls, pause, run_with_progress, text_input,
                 confirm, flash, paths_menu, pager)
from . import config as _c
from . import render


# ── MCP status ────────────────────────────────────────────────

def get_mcp_status():
    """Run 'claude mcp list', return list of (name, status) tuples."""
    claude_exe = get_claude_exe()
    if not claude_exe:
        return []
    try:
        r = subprocess.run(
                [claude_exe, 'mcp', 'list'],
                capture_output=True, text=True, timeout=10,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        lines = (r.stdout + r.stderr).splitlines()
        servers = []
        for line in lines:
            line = line.strip()
            if not line or line.lower().startswith('checking'):
                continue
            if '✔' in line or 'Connected' in line:
                name = line.split(':')[0].strip().replace('claude.ai ', '')
                servers.append((name, 'ok'))
            elif '!' in line or 'auth' in line.lower():
                name = line.split(':')[0].strip().replace('claude.ai ', '')
                servers.append((name, 'auth'))
        return servers
    except Exception:
        return []

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
    out, cancelled = run_with_progress(
        [claude_exe, '--print', prompt,
         '--disallowedTools', 'Write,Edit,NotebookEdit,Bash'],
        ('CLAUDECTL', mcp_name, 'MCP ANALYSIS'),
        f'Analyzing {mcp_name} MCP tools via Claude...  (15-60s)',
        timeout=120)
    if cancelled:
        return ''
    return (out or '').strip()


def update_global_claude_md_mcp(mcp_name, tools_doc):
    """Write/update MCP section in global CLAUDE.md using per-MCP sentinels."""
    start_tag = f'<!-- MCP:{mcp_name}:START -->'
    end_tag   = f'<!-- MCP:{mcp_name}:END -->'
    section   = f"{start_tag}\n## MCP: {mcp_name}\n{tools_doc}\n{end_tag}\n"

    existing = ''
    if os.path.exists(global_claude_md):
        try:
            existing = open(global_claude_md, encoding='utf-8', errors='ignore').read()
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

    try:
        with open(global_claude_md, 'w', encoding='utf-8') as f:
            f.write(final)
        return True
    except Exception:
        return False


def global_claude_md_menu():
    """Sub-menu: pick MCP to analyze, or edit global CLAUDE.md."""
    from . import config as _c
    mcp_items = []
    for name, status in mcp_servers:
        icon = f'{_c.C_OK}✔{_c.C_RESET}' if status == 'ok' else f'{_c.C_WARN}!{_c.C_RESET}'
        mcp_items.append((f"{icon}  {name}", f'mcp:{name}'))
    mcp_items += [(f"{'─' * W}", None), ('📝  Edit global CLAUDE.md in editor', '__edit__')]

    while True:
        sel = menu(mcp_items, "GLOBAL CLAUDE.md  /  Select MCP to analyze")
        if not sel:
            return
        if sel == '__edit__':
            if not os.path.exists(global_claude_md):
                with open(global_claude_md, 'w', encoding='utf-8') as f:
                    f.write('# Global Claude Context\n<!-- This file is read by Claude in every session -->\n\n')
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


def _mcp_run(args, label, crumbs=('CLAUDECTL', 'MCP')):
    """Run `claude mcp <args>` with progress. Returns (stdout, cancelled)."""
    claude = get_claude_exe()
    if not claude:
        return None, False
    return run_with_progress([claude, 'mcp', *args], crumbs, label, timeout=60)


def _list_servers():
    """Parsed server rows: [(name, status, raw_line)]."""
    rows = []
    for name, status in get_mcp_status():
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
            icon = f'{_c.C_OK}✔{_c.C_RESET}' if status == 'ok' else f'{_c.C_WARN}!{_c.C_RESET}'
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
