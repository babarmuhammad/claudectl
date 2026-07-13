import os
import json
import shutil
import logging

_USERPROFILE = os.environ.get('USERPROFILE') or os.path.expanduser('~')
_TEMP        = os.environ.get('TEMP') or os.environ.get('TMP') or _USERPROFILE

choice_file = os.environ.get('CHOICE_FILE', os.path.join(_TEMP, 'choice_claude.txt'))


# ── logging ──────────────────────────────────────────────────
# Quiet by default; file logging to %TEMP%\claudectl.log when CLAUDECTL_DEBUG
# is set. Background-thread/render failures log here instead of vanishing.
log = logging.getLogger('claudectl')
log.addHandler(logging.NullHandler())
if os.environ.get('CLAUDECTL_DEBUG'):
    try:
        _h = logging.FileHandler(os.path.join(_TEMP, 'claudectl.log'), encoding='utf-8')
        _h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        log.addHandler(_h)
        log.setLevel(logging.DEBUG)
    except Exception:
        pass

# ── user settings ────────────────────────────────────────────
# settings_file is FIXED under ~/.claude (account-independent) so the
# claude_config_dir selector can always be read regardless of which
# config dir is active.

settings_file = os.path.join(_USERPROFILE, '.claude', 'claudectl.json')

# Agent library — account-independent store of subagent .md files organized
# into category subfolders. NOT under projects/agents so Claude doesn't
# auto-load them; claudectl injects the chosen ones per session via --agents.
agents_library_dir = os.path.join(_USERPROFILE, '.claude', 'claudectl-agents')

_DEFAULT_SETTINGS = {
    'editor': '',              # path to preferred text editor ('' = auto-detect)
    'claude_exe': '',          # path to claude.exe ('' = auto-detect)
    'claude_config_dir': '',   # CLAUDE_CONFIG_DIR override ('' = default ~/.claude)
    'default_effort': '',      # preselected effort in launch options
    'default_model': '',       # preselected model in launch options
    'default_permission': '',  # preselected --permission-mode
    'project_defaults': {},    # encoded_name -> {'effort','model','permission'}
    'cost_table': {},          # user overrides for COST_PER_MTOK
    'theme': 'default',        # named palette (see THEMES)
    'memory_to_claudemd': True, # write semantic memory digest into CLAUDE.md
    'memory_max_calls': None,   # cap Claude calls when building memory (None = all)
    'memory_budget': 600,       # token budget for per-prompt recall injection
    'memory_rules': True,       # generate .claude/rules/claudectl-mem-*.md files
    'memory_prompt_hook': False, # UserPromptSubmit recall hook (global default)
    'memory_lessons': 'prompt', # session learning: 'off' | 'prompt' | 'auto'
    'memory_lessons_ttl': 30,   # evict unpinned lessons unused for N sessions
    'daily_token_alert': 0,     # warn badge when today's tokens cross this (0 = off)
    'agents_auto': 'suggest',   # agent suggestions: 'off' | 'suggest' | 'auto'
    'memory_max_entities': 500, # cap on stored entities (consolidation evicts by rank)
    'memory_auto_refresh': 'open',  # 'off' | 'open' (auto-refresh on project open) | 'hub'
    'memory_lessons_autoapprove': 0.8,  # lessons with confidence >= this auto-approve (0 = off)
    'conventions_to_global': True,  # promote cross-project conventions to ~/.claude/CLAUDE.md
    'plan_model': 'claude-opus-4-8',   # Plan→Execute: model that writes the plan
    'exec_model': 'claude-sonnet-5',   # Plan→Execute: model that executes it
    'accounts': [],                    # named Claude accounts: [{'name','dir'}]
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

# ── theme palette (256-color; switchable, see THEMES / apply_theme) ─
C_ACCENT    = '\033[38;5;117m'              # accent
C_SEL_BG    = '\033[48;5;237m\033[97m'      # selected row: bg + bright fg
C_HEADER_BG = '\033[48;5;24m\033[38;5;231m' # header bar: bg + fg
C_OK        = '\033[38;5;114m'              # success / connected
C_WARN      = '\033[38;5;215m'              # attention
C_ERR       = '\033[91m'                    # errors
C_NAME      = '\033[97m'                    # session names

# Named palettes. Each maps the switchable C_* entries; missing keys keep
# the default. Title/search/star also retint to the accent family.
THEMES = {
    'default': {'C_ACCENT': '\033[38;5;117m', 'C_SEL_BG': '\033[48;5;237m\033[97m',
                'C_HEADER_BG': '\033[48;5;24m\033[38;5;231m', 'C_OK': '\033[38;5;114m',
                'C_WARN': '\033[38;5;215m', 'C_TITLE': '\033[96m', 'C_SRCH': '\033[96;1m',
                'C_STAR': '\033[93m'},
    'ocean':   {'C_ACCENT': '\033[38;5;39m', 'C_SEL_BG': '\033[48;5;24m\033[97m',
                'C_HEADER_BG': '\033[48;5;23m\033[38;5;231m', 'C_OK': '\033[38;5;43m',
                'C_WARN': '\033[38;5;214m', 'C_TITLE': '\033[38;5;39m', 'C_SRCH': '\033[38;5;45;1m',
                'C_STAR': '\033[38;5;45m'},
    'forest':  {'C_ACCENT': '\033[38;5;78m', 'C_SEL_BG': '\033[48;5;22m\033[97m',
                'C_HEADER_BG': '\033[48;5;22m\033[38;5;231m', 'C_OK': '\033[38;5;120m',
                'C_WARN': '\033[38;5;179m', 'C_TITLE': '\033[38;5;78m', 'C_SRCH': '\033[38;5;120;1m',
                'C_STAR': '\033[38;5;185m'},
    'mono':    {'C_ACCENT': '\033[97m', 'C_SEL_BG': '\033[7m', 'C_HEADER_BG': '\033[7m',
                'C_OK': '\033[97m', 'C_WARN': '\033[97m', 'C_TITLE': '\033[1m',
                'C_SRCH': '\033[1m', 'C_STAR': '\033[97m'},
    'mocha':   {'C_ACCENT': '\033[38;5;141m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;60m\033[38;5;231m', 'C_OK': '\033[38;5;150m',
                'C_WARN': '\033[38;5;216m', 'C_TITLE': '\033[38;5;141m', 'C_SRCH': '\033[38;5;218;1m',
                'C_STAR': '\033[38;5;223m'},
    'tokyo':   {'C_ACCENT': '\033[38;5;111m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;189m',
                'C_HEADER_BG': '\033[48;5;24m\033[38;5;231m', 'C_OK': '\033[38;5;149m',
                'C_WARN': '\033[38;5;215m', 'C_TITLE': '\033[38;5;111m', 'C_SRCH': '\033[38;5;117;1m',
                'C_STAR': '\033[38;5;179m'},
    'dracula': {'C_ACCENT': '\033[38;5;135m', 'C_SEL_BG': '\033[48;5;238m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;61m\033[38;5;231m', 'C_OK': '\033[38;5;84m',
                'C_WARN': '\033[38;5;215m', 'C_TITLE': '\033[38;5;212m', 'C_SRCH': '\033[38;5;123;1m',
                'C_STAR': '\033[38;5;228m'},
    'nord':    {'C_ACCENT': '\033[38;5;109m', 'C_SEL_BG': '\033[48;5;239m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;60m\033[38;5;231m', 'C_OK': '\033[38;5;108m',
                'C_WARN': '\033[38;5;222m', 'C_TITLE': '\033[38;5;110m', 'C_SRCH': '\033[38;5;116;1m',
                'C_STAR': '\033[38;5;222m'},
    'gruvbox': {'C_ACCENT': '\033[38;5;214m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;223m',
                'C_HEADER_BG': '\033[48;5;94m\033[38;5;223m', 'C_OK': '\033[38;5;142m',
                'C_WARN': '\033[38;5;208m', 'C_TITLE': '\033[38;5;214m', 'C_SRCH': '\033[38;5;108;1m',
                'C_STAR': '\033[38;5;214m'},
    'rose':    {'C_ACCENT': '\033[38;5;183m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;189m',
                'C_HEADER_BG': '\033[48;5;66m\033[38;5;231m', 'C_OK': '\033[38;5;116m',
                'C_WARN': '\033[38;5;222m', 'C_TITLE': '\033[38;5;183m', 'C_SRCH': '\033[38;5;217;1m',
                'C_STAR': '\033[38;5;222m'},
    'catppuccin-latte': {'C_ACCENT': '\033[38;5;98m', 'C_SEL_BG': '\033[48;5;253m\033[38;5;236m',
                'C_HEADER_BG': '\033[48;5;62m\033[38;5;231m', 'C_OK': '\033[38;5;71m',
                'C_WARN': '\033[38;5;208m', 'C_TITLE': '\033[38;5;33m', 'C_SRCH': '\033[38;5;33;1m',
                'C_STAR': '\033[38;5;172m'},
    'kanagawa': {'C_ACCENT': '\033[38;5;110m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;223m',
                'C_HEADER_BG': '\033[48;5;60m\033[38;5;231m', 'C_OK': '\033[38;5;107m',
                'C_WARN': '\033[38;5;215m', 'C_TITLE': '\033[38;5;74m', 'C_SRCH': '\033[38;5;117;1m',
                'C_STAR': '\033[38;5;180m'},
    'everforest': {'C_ACCENT': '\033[38;5;108m', 'C_SEL_BG': '\033[48;5;237m\033[38;5;223m',
                'C_HEADER_BG': '\033[48;5;65m\033[38;5;231m', 'C_OK': '\033[38;5;144m',
                'C_WARN': '\033[38;5;179m', 'C_TITLE': '\033[38;5;108m', 'C_SRCH': '\033[38;5;116;1m',
                'C_STAR': '\033[38;5;173m'},
    'ayu':     {'C_ACCENT': '\033[38;5;208m', 'C_SEL_BG': '\033[48;5;238m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;24m\033[38;5;231m', 'C_OK': '\033[38;5;149m',
                'C_WARN': '\033[38;5;221m', 'C_TITLE': '\033[38;5;80m', 'C_SRCH': '\033[38;5;117;1m',
                'C_STAR': '\033[38;5;221m'},
    'monokai-pro': {'C_ACCENT': '\033[38;5;141m', 'C_SEL_BG': '\033[48;5;238m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;61m\033[38;5;231m', 'C_OK': '\033[38;5;149m',
                'C_WARN': '\033[38;5;215m', 'C_TITLE': '\033[38;5;117m', 'C_SRCH': '\033[38;5;204;1m',
                'C_STAR': '\033[38;5;222m'},
    'solarized': {'C_ACCENT': '\033[38;5;33m', 'C_SEL_BG': '\033[48;5;24m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;23m\033[38;5;231m', 'C_OK': '\033[38;5;142m',
                'C_WARN': '\033[38;5;178m', 'C_TITLE': '\033[38;5;37m', 'C_SRCH': '\033[38;5;62;1m',
                'C_STAR': '\033[38;5;178m'},
    'ember':   {'C_ACCENT': '\033[38;5;203m', 'C_SEL_BG': '\033[48;5;52m\033[38;5;231m',
                'C_HEADER_BG': '\033[48;5;88m\033[38;5;231m', 'C_OK': '\033[38;5;180m',
                'C_WARN': '\033[38;5;214m', 'C_TITLE': '\033[38;5;196m', 'C_SRCH': '\033[38;5;209;1m',
                'C_STAR': '\033[38;5;208m'},
}
THEME_NAMES = list(THEMES)


def apply_theme(name):
    """Switch the active palette. Unknown name → 'default'. Safe to call live."""
    g = globals()
    pal = THEMES.get(name) or THEMES['default']
    for k, v in pal.items():
        g[k] = v


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
MODELS        = ['', 'claude-haiku-4-5', 'claude-sonnet-5', 'claude-opus-4-8', 'claude-fable-5']
MODEL_LABELS  = ['default', 'haiku-4-5', 'sonnet-5', 'opus-4-8', 'fable-5']
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
_MEMORY_START   = '<!-- CLAUDECTL:MEMORY:START -->'
_MEMORY_END     = '<!-- CLAUDECTL:MEMORY:END -->'
_CONV_START     = '<!-- CLAUDECTL:CONVENTIONS:START -->'
_CONV_END       = '<!-- CLAUDECTL:CONVENTIONS:END -->'

_GMCP_START = '<!-- MCP:{name}:START -->'
_GMCP_END   = '<!-- MCP:{name}:END -->'
