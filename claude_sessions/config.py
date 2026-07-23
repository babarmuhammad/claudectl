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

# Skill library — account-independent store of user SKILL.md skill folders.
# Bundled starter templates ship in the package (skills_templates/); the
# library holds the user's own + any they save. Installed per-project into
# <project>/.claude/skills/ where Claude Code auto-discovers them on demand.
skills_library_dir = os.path.join(_USERPROFILE, '.claude', 'claudectl-skills')

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
    'extract_model': 'claude-haiku-4-5',  # economy model for claudectl's OWN internal
                                          # calls (memory/lessons/CLAUDE.md/agent/hook/
                                          # skill generation). '' = account default.
    'review_model': '',                # code-review model ('' = fall back to exec_model)
    'review_min_confidence': 80,       # code-review: drop findings below this (0-100)
    'accounts': [],                    # named Claude accounts: [{'name','dir'}]
    'claude_md_sessions_cap': 10,  # SESSIONS block: keep most recent N (0 = unlimited)
    'claude_md_commits': 7,        # AUTOGEN block: git log -N per repo
    'default_max_thinking': '',    # MAX_THINKING_TOKENS env for launches ('' = unset)
    'default_subagent_model': '',  # CLAUDE_CODE_SUBAGENT_MODEL env ('' = unset)
    'ui_mode': 'tui',              # default interface: 'tui' | 'gui' (desktop app)
    'gui_shell': 'auto',           # GUI window: 'auto' | 'qt' | 'edge' | 'browser'
    'auto_memory_interval': 3600,  # GUI background auto-memory re-check cadence (s)
    'omniroute_base_url':   'http://localhost:20128',  # local OmniRoute proxy
    'omniroute_api_key':    '',   # -> ANTHROPIC_AUTH_TOKEN for the exec session
    'omniroute_exec_model': '',   # Plan→Execute exec model, routed free-tier via
                                   # OmniRoute instead of the real Anthropic API.
                                   # '' = disabled (exec_model/real API as usual).
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
        with open(settings_file, 'r', encoding='utf-8-sig') as f:
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


def all_config_dirs():
    """[(name, dir)] for every known account (default first), deduped by
    resolved path — so session discovery can see sessions from every account,
    not just whichever one is currently active."""
    default = os.path.join(_USERPROFILE, '.claude')
    candidates = [('default', default)]
    for a in load_settings().get('accounts', []):
        if isinstance(a, dict) and a.get('dir'):
            candidates.append((a.get('name', a['dir']),
                               os.path.expanduser(os.path.expandvars(a['dir']))))
    seen, out = set(), []
    for name, d in candidates:
        rp = os.path.normcase(os.path.abspath(d))
        if rp in seen:
            continue
        seen.add(rp)
        out.append((name, d))
    return out


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
# launch-economy: cap thinking tokens (MAX_THINKING_TOKENS env) to cut cost on
# routine work; '' = leave the model's default budget alone.
THINKING_CAPS   = ['',        '4000', '8000', '16000', '32000']
THINKING_LABELS = ['default', '4k', '8k', '16k', '32k']

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

# ── model economy guide (launch picker) ──────────────────────────────────
# Practical cost/capability profiles for the curated launch roster. Cost bars
# are derived from COST_PER_MTOK so they stay in sync with pricing; capability
# and guidance reflect Anthropic's July-2026 tuning notes (medium = sweet spot,
# high/xhigh for coding-agentic, sonnet handles ~90% of coding at ~60% of Opus
# cost, escalate to Opus for deep refactor / hard debugging).
# swe = SWE-bench Verified %, cap = relative capability 1-5. Grounded in
# July-2026 benchmarks (Haiku 73 / Sonnet-5 85 / Opus-4.8 89; Fable top, no
# public SWE score). speed labels per Anthropic (Haiku fastest, Sonnet Fast,
# Opus Moderate, Fable slow/deep).
MODEL_PROFILES = {
    'claude-haiku-4-5': {'cap': 2, 'swe': 73, 'speed': 'fast', 'best_for': 'bulk, simple edits, subagents'},
    'claude-sonnet-5':  {'cap': 4, 'swe': 85, 'speed': 'fast', 'best_for': 'default coding (~90% of tasks)'},
    'claude-opus-4-8':  {'cap': 5, 'swe': 89, 'speed': 'med',  'best_for': 'deep refactor, hard debugging'},
    'claude-fable-5':   {'cap': 5, 'swe': None, 'speed': 'slow', 'best_for': 'hardest, longest-horizon work'},
}
EFFORT_PROFILES = {
    '':       'account default',
    'low':    'simple / subagents / cheap',
    'medium': 'balanced — sweet spot',
    'high':   'complex work, thorough',
    'xhigh':  'best for coding & agentic',
    'max':    'maximum depth, priciest',
}
# task-based quick-start presets: (name, description, opts-fields)
LAUNCH_PRESETS = [
    ('Recommended',   'everyday coding — best balance',
     {'model': 'claude-sonnet-5', 'effort': 'high'}),
    ('Cheap & fast',  'simple / bulk work, lowest cost',
     {'model': 'claude-sonnet-5', 'effort': 'low', 'subagent_model': 'claude-haiku-4-5'}),
    ('Deep reasoning', 'hard refactor, accuracy-critical',
     {'model': 'claude-opus-4-8', 'effort': 'xhigh'}),
    ('Max capability', 'hardest, longest-horizon',
     {'model': 'claude-fable-5', 'effort': 'high'}),
]


def _model_price_in(model):
    m = (model or '').replace('claude-', '')
    for key, v in COST_PER_MTOK.items():
        if key in m:
            return v['in']
    return None


def cost_bar(model):
    """'$'..'$$$$$' by input price; '' for account-default/unknown."""
    p = _model_price_in(model)
    if p is None:
        return ''
    tier = 1 if p <= 1 else 2 if p <= 3 else 3 if p <= 5 else 5
    return '$' * tier


def cap_bar(model):
    """'▪' capability bar (1-5); '' for account-default/unknown."""
    prof = MODEL_PROFILES.get(model)
    return '▪' * (prof['cap'] if prof else 0)


def swe_str(model):
    """'85%' SWE-bench score, or '—' when unknown/account-default."""
    prof = MODEL_PROFILES.get(model)
    if not prof or prof.get('swe') is None:
        return '—'
    return f"{prof['swe']}%"


def model_card_rows():
    """[(model_id, label, cost_bar, cap_bar, best_for, swe_str)] for the roster."""
    rows = []
    for mid in MODELS:
        prof = MODEL_PROFILES.get(mid)
        if not mid or not prof:
            continue
        rows.append((mid, MODEL_LABELS[MODELS.index(mid)],
                     cost_bar(mid), cap_bar(mid), prof['best_for'], swe_str(mid)))
    return rows


def advise(model, effort):
    """Dynamic launch advisor. Returns (level, message) where level is
    'ok' | 'tip' | 'warn'; names a better model/effort when the pick is
    sub-optimal. Grounded in July-2026 cost/quality data."""
    eff = effort or ''
    ei = EFFORTS.index(eff) if eff in EFFORTS else 0    # 0 default,1 low,2 med,3 high,4 xhigh,5 max
    if not MODEL_PROFILES.get(model):
        return ('tip', 'Pick a model — Sonnet 5 · high is the recommended default.')
    if model == 'claude-opus-4-8' and ei in (1, 2):
        return ('tip', 'Opus is underused at this effort — Sonnet 5 · high gives ~similar quality at ~60% less cost.')
    if model == 'claude-sonnet-5' and ei >= 4:
        return ('warn', 'Sonnet at xhigh burns heavy reasoning tokens — can cost more than Opus 4.8 · high for similar quality. Use Opus · high or Sonnet · high.')
    if model == 'claude-fable-5' and ei < 4:
        return ('tip', 'Fable is the priciest tier — Opus 4.8 · xhigh handles almost everything at half the cost.')
    if model == 'claude-haiku-4-5' and ei >= 3:
        return ('warn', "Haiku isn't built for deep reasoning — switch to Sonnet 5 for hard tasks.")
    good = {
        'claude-haiku-4-5': 'Cheapest & fastest — great for bulk, simple edits, and subagents.',
        'claude-sonnet-5':  'Best default — ~90% of coding at Opus quality; high ≈ Opus low.',
        'claude-opus-4-8':  'Top accuracy tier — deep refactor & hard debugging; xhigh is the coding sweet spot.',
        'claude-fable-5':   'Maximum capability for the hardest, longest-horizon work.',
    }
    return ('ok', good.get(model, ''))


def omniroute_env(s=None):
    """{} when free-tier exec routing is off; else the ANTHROPIC_BASE_URL/
    AUTH_TOKEN override that points an interactive `claude` launch at
    OmniRoute (github.com/diegosouzapw/OmniRoute) instead of the real
    Anthropic API. Only ever used for the execution half of Plan→Execute —
    planning always stays on the real API (see plan_execute.py)."""
    s = load_settings() if s is None else s
    if not s.get('omniroute_exec_model'):
        return {}
    return {'ANTHROPIC_BASE_URL': s.get('omniroute_base_url') or '',
            'ANTHROPIC_AUTH_TOKEN': s.get('omniroute_api_key') or ''}


# Ordered stops on the cost/quality frontier for the GUI's single-slider
# model picker — deliberately curated to the advisor's 'good' combos (not
# every advise()=='ok' pairing) so each stop is a genuine step up in
# capability/cost, cheapest to most powerful. A bad combo (Sonnet·xhigh,
# Opus·low, …) simply isn't reachable from this control.
MODEL_EFFORT_FRONTIER = [
    ('claude-haiku-4-5', 'low'),
    ('claude-sonnet-5',  'medium'),
    ('claude-sonnet-5',  'high'),
    ('claude-opus-4-8',  'high'),
    ('claude-opus-4-8',  'xhigh'),
    ('claude-fable-5',   'xhigh'),
    ('claude-fable-5',   'max'),
]


def frontier_rows():
    """[(model, effort, label, cost_bar, swe_str, note)] for each frontier
    stop, cheap→max-power, for the GUI's single frontier slider."""
    rows = []
    for mid, eff in MODEL_EFFORT_FRONTIER:
        _level, note = advise(mid, eff)
        rows.append((mid, eff, MODEL_LABELS[MODELS.index(mid)],
                     cost_bar(mid), swe_str(mid), note))
    return rows


def active_preset(opts):
    """Name of the preset whose fields all match opts, else None."""
    for name, _desc, fields in LAUNCH_PRESETS:
        if all((opts.get(k) or '') == v for k, v in fields.items()):
            return name
    return None

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
