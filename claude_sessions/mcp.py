import os
import subprocess
import threading
import time

from .config import W, global_claude_md
from .sessions import get_session_info
from .ui import menu


# ── MCP status ────────────────────────────────────────────────

def get_mcp_status():
    """Run 'claude mcp list', return list of (name, status) tuples."""
    claude_exe = os.path.join(os.environ['USERPROFILE'], '.local', 'bin', 'claude.exe')
    try:
        r = subprocess.run([claude_exe, 'mcp', 'list'],
                           capture_output=True, text=True, timeout=10)
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

def _mcp_background():
    global mcp_servers, _mcp_ready
    mcp_servers = get_mcp_status()
    _mcp_ready = True

threading.Thread(target=_mcp_background, daemon=True).start()


# ── global CLAUDE.md / MCP analysis ──────────────────────────

def analyze_mcp_tools(mcp_name):
    """Run claude --print to get MCP tool list. Shows progress. Returns markdown string."""
    claude_exe = os.path.join(os.environ['USERPROFILE'], '.local', 'bin', 'claude.exe')
    prompt = (
        f"Using the {mcp_name} MCP server, call the tools/list endpoint and list every available tool. "
        f"For each tool output: tool name, one-line description, and key parameters. "
        f"Format as markdown. Be concise. No intro text."
    )
    os.system('cls')
    print(f"\n  Analyzing {mcp_name} MCP tools via Claude...\n  (this may take 15-30s)\n")
    try:
        r = subprocess.run([claude_exe, '--print', prompt],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return ''
    except Exception as e:
        return ''


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
    mcp_items = []
    for name, status in mcp_servers:
        icon = '✔' if status == 'ok' else '☆'
        mcp_items.append((f"{icon}  {name}", f'mcp:{name}'))
    mcp_items += [(f"{'─' * W}", None), ('📝  Edit global CLAUDE.md in Notepad', '__edit__')]

    while True:
        sel = menu(mcp_items, "GLOBAL CLAUDE.md  /  Select MCP to analyze")
        if not sel:
            return
        if sel == '__edit__':
            if not os.path.exists(global_claude_md):
                with open(global_claude_md, 'w', encoding='utf-8') as f:
                    f.write('# Global Claude Context\n<!-- This file is read by Claude in every session -->\n\n')
            subprocess.Popen([r'C:\Program Files\Notepad++\notepad++.exe',global_claude_md])
            return
        if sel.startswith('mcp:'):
            mcp_name = sel[4:]
            tools_doc = analyze_mcp_tools(mcp_name)
            if tools_doc:
                ok = update_global_claude_md_mcp(mcp_name, tools_doc)
                os.system('cls')
                if ok:
                    print(f"\n  ✔ Written to {global_claude_md}\n")
                    print(f"  Claude will see {mcp_name} tool docs in every session.\n")
                    subprocess.Popen([r'C:\Program Files\Notepad++\notepad++.exe',global_claude_md])
                else:
                    print(f"\n  ✘ Failed to write {global_claude_md}\n")
            else:
                os.system('cls')
                print(f"\n  ✘ No output from Claude — MCP may need authentication.\n")
            input("  Press Enter to continue...")
            return


def mcp_status_line():
    if not _mcp_ready:
        return '  MCP: checking...'
    connected = [name for name, status in mcp_servers if status == 'ok']
    if not connected:
        return ''
    return '  MCP: ' + '   '.join(f'✔ {n}' for n in connected)
