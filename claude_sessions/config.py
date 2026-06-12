import os
import json
import shutil

_USERPROFILE = os.environ.get('USERPROFILE') or os.path.expanduser('~')
_TEMP        = os.environ.get('TEMP') or os.environ.get('TMP') or _USERPROFILE

choice_file = os.environ.get('CHOICE_FILE', os.path.join(_TEMP, 'choice_claude.txt'))

# ── user settings ────────────────────────────────────────────
# settings_file is FIXED under ~/.claude (account-independent) so the
# claude_config_dir selector can always be read regardless of which
# config dir is active.

settings_file = os.path.join(_USERPROFILE, '.claude', 'claudectl.json')

_DEFAULT_SETTINGS = {
    'editor': '',              # path to preferred text editor ('' = auto-detect)
    'claude_exe': '',          # path to claude.exe ('' = auto-detect)
    'claude_config_dir': '',   # CLAUDE_CONFIG_DIR override ('' = default ~/.claude)
    'default_effort': '',      # preselected effort in launch options
    'default_model': '',       # preselected model in launch options
    'default_permission': '',  # preselected --permission-mode
    'project_defaults': {},    # encoded_name -> {'effort','model','permission'}
    'cost_table': {},          # user overrides for COST_PER_MTOK
}


def _norm_model(m):
    """Migrate legacy bare model strings ('sonnet-4-6') to full ids."""
    if m and not m.startswith('claude-'):
        return 'claude-' + m
    return m


def load_settings():
    """Read ~/.claude/claudectl.json, merged over defaults. Never raises."""
    s = dict(_DEFAULT_SETTINGS)
    try:
        with open(settings_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update({k: v for k, v in data.items() if k in _DEFAULT_SETTINGS})
    except Exception:
        pass
    # normalize legacy model ids saved by older versions
    s['default_model'] = _norm_model(s.get('default_model', ''))
    pd = s.get('project_defaults')
    if isinstance(pd, dict):
        for v in pd.values():
            if isinstance(v, dict) and v.get('model'):
                v['model'] = _norm_model(v['model'])
    return s


def save_settings(s):
    """Write settings dict. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(settings_file), exist_ok=True)
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
        return True
    except Exception:
        return False


# ── active config dir ────────────────────────────────────────
# claude_config_dir setting drives BOTH session browsing (projects_dir)
# and the CLAUDE_CONFIG_DIR env handed to claude.exe at launch, so the
# whole tool works against one account/config dir at a time.

def get_config_dir():
    """Resolve active CLAUDE_CONFIG_DIR. Setting > default ~/.claude."""
    override = load_settings().get('claude_config_dir', '')
    if override:
        return os.path.expanduser(os.path.expandvars(override))
    return os.path.join(_USERPROFILE, '.claude')


config_dir        = get_config_dir()
projects_dir      = os.path.join(config_dir, 'projects')
last_session_file = os.path.join(projects_dir, 'last-session.json')
global_claude_md  = os.path.join(config_dir, 'CLAUDE.md')


# ── executable discovery ────────────────────────────────────

def find_editor():
    """Best available text editor. Settings override > Notepad++ > VS Code > notepad."""
    override = load_settings().get('editor', '')
    if override and os.path.exists(override):
        return override
    candidates = [
        r'C:\Program Files\Notepad++\notepad++.exe',
        r'C:\Program Files (x86)\Notepad++\notepad++.exe',
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Notepad++', 'notepad++.exe'),
        shutil.which('notepad++'),
        shutil.which('code'),
        os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'notepad.exe'),
        shutil.which('notepad'),
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            return exe
    return None


def open_in_editor(path):
    """Open path in the best available editor. Returns True if launched."""
    import subprocess
    editor = find_editor()
    if not editor:
        return False
    try:
        subprocess.Popen([editor, path])
        return True
    except Exception:
        return False


def get_claude_exe():
    """Locate claude.exe. Settings override > default install path > PATH. None if missing."""
    override = load_settings().get('claude_exe', '')
    if override and os.path.exists(override):
        return override
    default = os.path.join(_USERPROFILE, '.local', 'bin', 'claude.exe')
    if os.path.exists(default):
        return default
    for name in ('claude.exe', 'claude'):
        found = shutil.which(name)
        if found:
            return found
    return None

# ── ANSI colors ──────────────────────────────────────────────
C_RESET  = '\033[0m'
C_TITLE  = '\033[96m'     # cyan — titles / headers
C_SEL    = '\033[93m'     # yellow — selected > marker
C_DIM    = '\033[90m'     # dark gray — separators, hints, age
C_STAR   = '\033[93m'     # yellow — ★☆ stars
C_GREEN  = '\033[92m'     # green — MCP connected
C_BOLD   = '\033[1m'      # bold
C_SRCH   = '\033[96;1m'   # bright cyan bold — active search bar

# ── theme palette (256-color; see use_16color_fallback) ─────
C_ACCENT    = '\033[38;5;117m'              # light blue accent
C_SEL_BG    = '\033[48;5;237m\033[97m'      # selected row: gray bg, bright fg
C_HEADER_BG = '\033[48;5;24m\033[38;5;231m' # header bar: deep blue bg, white fg
C_OK        = '\033[38;5;114m'              # soft green — connected / success
C_WARN      = '\033[38;5;215m'              # orange — needs attention
C_ERR       = '\033[91m'                    # red — errors
C_NAME      = '\033[97m'                    # bright white — session names


def use_16color_fallback():
    """Swap 256-color theme entries for classic 16-color codes (old conhost)."""
    global C_ACCENT, C_SEL_BG, C_HEADER_BG, C_OK, C_WARN
    C_ACCENT    = '\033[96m'
    C_SEL_BG    = '\033[7m'        # reverse video
    C_HEADER_BG = '\033[46;30m'    # cyan bg, black fg
    C_OK        = '\033[92m'
    C_WARN      = '\033[93m'

BAD_PREFIXES = ('<', '[', 'I0', 'W0', 'E0', 'Caveat', 'Base directory', 'session')
BAD_CONTAINS = ['.claude', 'plugins', 'interrupted by user', 'tool use', 'local-command']
W = 62

EFFORTS       = ['',        'low', 'medium', 'high', 'xhigh', 'max']
EFFORT_LABELS = ['default', 'low', 'medium', 'high', 'xhigh', 'max']
# Full model ids — claude.exe rejects bare version strings like 'sonnet-4-6'
MODELS        = ['', 'claude-haiku-4-5', 'claude-sonnet-4-6', 'claude-opus-4-8', 'claude-fable-5']
MODEL_LABELS  = ['default', 'haiku-4-5', 'sonnet-4-6', 'opus-4-8', 'fable-5']
PERMS         = ['',        'plan', 'acceptEdits', 'bypassPermissions', 'dontAsk']
PERM_LABELS   = ['default', 'plan', 'acceptEdits', 'bypassPermissions', 'dontAsk']
PERM_RISKY    = {'bypassPermissions', 'dontAsk'}   # shown with warning tint

# ── cost estimation ($ per MTok; substring-matched on message.model) ──
COST_PER_MTOK = {
    'fable-5':    {'in': 10.0, 'out': 50.0},
    'opus-4':     {'in': 5.0,  'out': 25.0},
    'sonnet-4-6': {'in': 3.0,  'out': 15.0},
    'sonnet':     {'in': 3.0,  'out': 15.0},
    'haiku-4-5':  {'in': 1.0,  'out': 5.0},
    'haiku':      {'in': 1.0,  'out': 5.0},
}
CACHE_READ_MULT  = 0.1
CACHE_WRITE_MULT = 1.25

_AUTOGEN_START  = '<!-- AUTOGEN:START -->'
_AUTOGEN_END    = '<!-- AUTOGEN:END -->'
_SESSIONS_START = '<!-- SESSIONS:START -->'
_SESSIONS_END   = '<!-- SESSIONS:END -->'
_AI_MARKER      = '<!-- AI:ANALYZED -->'

_GMCP_START = '<!-- MCP:{name}:START -->'
_GMCP_END   = '<!-- MCP:{name}:END -->'
