import os
import json
import shutil
import logging

from . import themes as _themes

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

#: where load_settings parks keys it does not recognise so save_settings can
#: put them back. Not a setting itself — never written under this name.
_UNKNOWN_KEYS = '_unknown'

_DEFAULT_SETTINGS = {
    'editor': '',              # path to preferred text editor ('' = auto-detect)
    'claude_exe': '',          # path to claude.exe ('' = auto-detect)
    'claude_config_dir': '',   # CLAUDE_CONFIG_DIR override ('' = default ~/.claude)
    'default_effort': '',      # preselected effort in launch options
    'default_model': '',       # preselected model in launch options
    'default_permission': 'auto',  # preselected --permission-mode (see PERMS)
    'perm_default_migrated': False,  # one-time '' -> 'auto' flip, see migrate_settings
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
    'plan_model': 'claude-opus-5',     # Plan→Execute: model that writes the plan
    'plan_timeout_sec': 900,           # cap on one headless plan-generation call
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
    'launch_fallback_models': [],  # --fallback-model chain when the primary is
                                   # overloaded. Unrelated to failover_models,
                                   # which is claudectl's OWN proxy for a
                                   # free-tier model deregistered upstream.
    'launch_autocompact': '',      # --autocompact: 'auto' | '200k' | ... ('' = unset)
    'headless_budget_usd': 0,      # --max-budget-usd cap on claudectl's OWN
                                   # `claude -p` calls (0 = no cap)
    'ui_mode': 'tui',              # default interface: 'tui' | 'gui' (desktop app)
    'gui_shell': 'auto',          # GUI window: 'auto' | 'qt' | 'edge' | 'browser'
    #: 'notify' = say so in the banner | 'auto' = also install it on quit |
    #: 'off' = no outbound check at all. ONE switch on purpose: it gates both the
    #: PyPI version check and the /v1/models catalogue refresh, because both are
    #: the same consent (claudectl talking to the network on your behalf) and a
    #: second switch is a second thing to desync.
    'auto_update': 'notify',
    # ── GUI appearance ──
    # These MUST be declared here, not just accepted by the POST handler.
    # load_settings() drops any key it does not know, and /api/settings does
    # load -> mutate -> save, so an undeclared key was written once and then
    # wiped by the very next settings save. Symptom: "when i close and open the
    # app it goes back to classic theme" — the world was saved, then deleted by
    # the next POST. `theme` was declared and survived, which is exactly why it
    # was the only one that appeared to work.
    'motion': 'full',
    #: names of the sidebar nav groups the user has collapsed. Declared HERE for
    #: the same reason every other appearance key is: load_settings drops what it
    #: does not know and /api/settings does load->mutate->save, so an undeclared
    #: key is written once and deleted by the next save.
    'nav_collapsed': [],
    #: sidebar width in px, 0 = "never dragged, use the CSS default". Kept as 0
    #: rather than 280 so the default lives in exactly one place (app.css).
    'side_w': 0,
    'nav_h': 0,     # sidebar nav block height in px, 0 = CSS default (same rule)
    'skin': '',
    'stage': '',
    'world': '',
    'surface': 0,
    # ── OpenTelemetry export (team observability) ──
    # Declared here for the same reason the appearance keys are: load_settings
    # drops anything it does not know, and /api/settings does load->mutate->save,
    # so an undeclared key is written once and deleted by the next save.
    'otel_enabled': False,
    'otel_endpoint': '',
    'otel_protocol': 'http/protobuf',
    'otel_headers': '',           # e.g. "Authorization=Bearer <token>"
    'auto_memory_interval': 3600,  # GUI background auto-memory re-check cadence (s)
    # ── alternate model backend ──
    # ONE active backend at a time, globally — deliberately, not a limitation
    # waiting to be lifted. failover.py (and gateway.py) resolve their upstream
    # by re-reading these keys PER REQUEST, so two concurrently-active backends
    # would hand one session's credential to the other's upstream.
    'provider_base_url':   'http://localhost:20128',  # Anthropic-shaped endpoint
    'provider_api_key':    '',   # -> ANTHROPIC_AUTH_TOKEN for the routed session
    'provider_exec_model': '',   # Plan→Execute exec model, routed through the
                                  # provider instead of the real Anthropic API.
                                  # '' = disabled (exec_model/real API as usual).
    #: '' = Anthropic direct (default, nothing is overridden at all)
    #: 'omniroute' = daemon auto-start, live /v1/models catalogue, circuit health
    #: 'generic'   = any already-running Anthropic-shaped URL (Ollama >= 0.14,
    #:               vLLM, llama.cpp, OpenRouter's Anthropic endpoint, a remote
    #:               server). No catalogue exists for these, so the model id is
    #:               free text and health is one real POST /v1/messages.
    'provider_kind':       '',
    #: user-entered context window of the routed model. 0 = unknown, no advisory.
    #: NOT probed from /v1/models: most catalogues omit context_length, and a
    #: fabricated number is worse than none.
    'provider_context_tokens': 0,
    #: -> ENABLE_TOOL_SEARCH=true. Claude Code disables MCP tool search on any
    #: non-first-party base URL; re-enabling it only works when the upstream
    #: forwards tool_reference blocks, so this is opt-in rather than implied by
    #: provider_kind.
    'provider_tool_search': False,
    'provider_keys_migrated': False,  # one-time omniroute_* rename, see migrate_settings
    # ── translating gateway (OpenAI-shaped backends) ──
    #: '' = off. 'openai' = claudectl runs a local proxy that speaks the
    #: Anthropic Messages API to `claude` and OpenAI Chat Completions upstream,
    #: for backends that never implemented /v1/messages (LM Studio, most bare
    #: local servers). When on, provider_base_url points AT the gateway.
    'gateway_kind': '',
    'gateway_port': 20130,            # loopback (failover: 20129, OmniRoute: 20128)
    'gateway_target_base_url': '',    # the OpenAI-shaped upstream
    #: SEPARATE from provider_api_key on purpose: the gateway's upstream and a
    #: direct provider connection are not guaranteed to be the same host, let
    #: alone the same credential.
    'gateway_target_api_key': '',
    'failover_models': [],        # ordered model ids claudectl's own proxy retries
                                   # when a turn errors before any byte reaches the
                                   # client. [] = feature off (no separate flag —
                                   # a second switch is a second thing to desync).
    'failover_port':   20129,     # loopback port for that proxy (OmniRoute: 20128)
    'failover_quiet':  False,     # True = hide the proxy console window
}


#: settings the GUI's generic key loop must NOT accept straight off the wire.
#: Two reasons, and every name here has one: it is clamped or typed by an
#: explicit sanitizer (two owners for one key is how a sanitizer gets bypassed),
#: or it is state claudectl writes for itself and no form should post.
#: `gui._SETTING_KEYS` is `_DEFAULT_SETTINGS` minus this — derived, so a new
#: setting is reachable from the GUI the moment it is declared, instead of
#: needing a second table nobody remembers to edit.
INTERNAL_SETTINGS = frozenset({
    '_unknown',                      # the carry-through bucket, not a setting
    'accounts', 'project_defaults', 'cost_table',
    'perm_default_migrated', 'provider_keys_migrated',
    'ui_mode',                       # handled first, validated against two values
    'provider_api_key',              # write-only: never echoed back to be resubmitted
    'gateway_target_api_key',        # same
    'failover_models', 'launch_fallback_models',  # list sanitizers
    'nav_collapsed', 'side_w', 'nav_h',           # geometry clamps
    'headless_budget_usd',                        # float clamp
    'memory_budget',                 # owned by /api/memory/toggles
})


def _norm_model(m):
    """Migrate legacy bare model strings ('sonnet-4-6') to full ids."""
    if m and not m.startswith('claude-'):
        return 'claude-' + m
    return m


def load_settings():
    """Read ~/.claude/claudectl.json, merged over defaults. Never raises."""
    from . import jsonstore     # lazy: jsonstore imports this module
    s = dict(_DEFAULT_SETTINGS)
    data = jsonstore.load(settings_file, expect=dict)
    s.update({k: v for k, v in data.items() if k in _DEFAULT_SETTINGS})
    # Keys this version does not know are carried, not dropped. save_settings
    # writes back whatever load_settings returned, so filtering them out here
    # meant an older claudectl silently ERASED a newer one's settings — and a
    # downgrade, or two machines syncing this file, is enough to trigger it.
    unknown = {k: v for k, v in data.items() if k not in _DEFAULT_SETTINGS}
    if unknown:
        s[_UNKNOWN_KEYS] = unknown
    # normalize legacy model ids saved by older versions
    s['default_model'] = _norm_model(s.get('default_model', ''))
    pd = s.get('project_defaults')
    if isinstance(pd, dict):
        for v in pd.values():
            if isinstance(v, dict) and v.get('model'):
                v['model'] = _norm_model(v['model'])
    return s


def migrate_settings(s):
    """One-time forward migrations of a loaded settings dict.

    Returns (settings, changed). PURE — the caller saves. It is deliberately not
    folded into load_settings(): that function is called on nearly every code
    path including read-only ones, and a read that writes is a cache with no
    invalidation waiting to happen.

    The permission default. Bumping _DEFAULT_SETTINGS['default_permission'] to
    'auto' reaches nobody who has already run claudectl: load_settings() lets a
    stored '' win over the default, and a stored per-project '' wins over THAT at
    launch time. The flag makes it one-time, so a user who later chooses '' back
    is not overruled on next start.

    The omniroute_* -> provider_* rename. The old names are read out of
    _UNKNOWN_KEYS, not off the top level: by the time this runs load_settings()
    has already parked them there (they are no longer in _DEFAULT_SETTINGS), and
    they are popped rather than left, because save_settings() layers _unknown
    back over the output and would otherwise rewrite the dead names forever.
    """
    changed = False
    if not s.get('provider_keys_migrated'):
        old = s.get(_UNKNOWN_KEYS) or {}
        for legacy, new in (('omniroute_base_url', 'provider_base_url'),
                            ('omniroute_api_key', 'provider_api_key'),
                            ('omniroute_exec_model', 'provider_exec_model')):
            if legacy in old:
                v = old.pop(legacy)
                if v:
                    s[new] = v
        # A configured exec model is what "routing was on" meant before there
        # was a kind to name; anything else stays off, which is the default.
        if s.get('provider_exec_model'):
            s['provider_kind'] = 'omniroute'
        s['provider_keys_migrated'] = True
        changed = True
    if not s.get('perm_default_migrated'):
        if not (s.get('default_permission') or ''):
            s['default_permission'] = 'auto'
        pd = s.get('project_defaults')
        if isinstance(pd, dict):
            for v in pd.values():
                if isinstance(v, dict) and not (v.get('permission') or ''):
                    v['permission'] = 'auto'
        s['perm_default_migrated'] = True
        changed = True
    return s, changed


def effective_perm(perm, model='', routed=''):
    """The --permission-mode value to actually pass, or '' to pass no flag.

    Only `auto` is ever suppressed, and only where the classifier cannot work:

    - A routed provider: prepare_launch() repoints ANTHROPIC_BASE_URL at it, and
      the auto-mode classifier is a SEPARATE model request that would be routed
      there too — to a backend that does not serve it.
    - An unsupported model (AUTO_UNSUPPORTED_MODELS).

    In both cases Claude Code would start the session in manual anyway; not
    passing the flag means the picker is not claiming a mode the session is not
    in. Every other mode passes through untouched.
    """
    if perm != 'auto':
        return perm
    if routed or model in AUTO_UNSUPPORTED_MODELS:
        return ''
    return perm


def launch_defaults(enc=''):
    """(model, perm) for a launch with NO options picker — context injection and
    the GUI's mirror of it. Permission resolves project-over-account and drops
    `auto` where the classifier cannot run.

    The MODEL deliberately ignores the project pin and takes the account
    default. That pin is only ever whatever the launch picker last saved, and
    the picker re-saves it on every launch, so one "try Fable on this project"
    made every later context-inject start on the priciest model — on a path that
    shows no picker, so nothing displayed the choice and nothing could change
    it. Permission does NOT get the same treatment: its pin can only be read as
    a restriction (a project set to `plan` must not silently launch looser), so
    honouring it there is safe where honouring the model is not.
    """
    s = load_settings()
    proj = (s.get('project_defaults') or {}).get(enc or '') or {}
    model = s.get('default_model', '')
    return model, effective_perm(proj.get('permission', s.get('default_permission', '')), model)


def perm_note(perm, model='', routed=''):
    """('level', 'message') for the permission row, or ('', '') when there is
    nothing to say. Kept OUT of advise(): the GUI precomputes advise() as a
    model x effort matrix, and a third axis would multiply it."""
    if perm == 'auto' and not effective_perm(perm, model, routed):
        why = ('the classifier would be routed at the provider too'
               if routed else f'{model or "this model"} does not support auto mode')
        return ('warn', f'auto is unavailable here ({why}) — this session starts in manual.')
    if perm == 'bypassPermissions':
        return ('warn', 'bypassPermissions skips every check — containers and VMs only.')
    return ('', '')


def write_atomic(path, text):
    """Temp file in the same directory, then os.replace. Returns True on success.

    Several of the files claudectl writes are parsed by Claude Code itself
    (settings.json carries hooks, permissions and outputStyle), so a crash, a
    full disk, or a killed process partway through a plain open(path,'w') leaves
    truncated JSON that breaks the user's whole session, not just claudectl.
    os.replace is atomic on NTFS, so a reader sees either the old file or the new
    one — never half of either."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False


def write_json_atomic(path, obj, indent=2):
    return write_atomic(path, json.dumps(obj, indent=indent))


def save_settings(s):
    """Write settings dict. Returns True on success."""
    out = {k: v for k, v in s.items() if k != _UNKNOWN_KEYS}
    out.update(s.get(_UNKNOWN_KEYS) or {})
    return write_json_atomic(settings_file, out)


# ── active config dir ────────────────────────────────────────
# claude_config_dir setting drives BOTH session browsing (projects_dir)
# and the CLAUDE_CONFIG_DIR env handed to claude.exe at launch, so the
# whole tool works against one account/config dir at a time.

def get_config_dir():
    """Resolve the active config dir. Env > setting > default ~/.claude.

    The env var comes FIRST, and that ordering is the whole point: claudectl
    sets `CLAUDE_CONFIG_DIR` in the child environment at five spawn sites
    (accounts, context_inject, plan_execute, gui_api) to choose the account,
    but nothing ever read it back. So a claudectl process running INSIDE a
    session launched under another account — the statusline, which runs on
    every turn — resolved the saved setting instead and reported the wrong
    account for the whole session. Env-first is also Claude Code's own
    precedence, so the two agree about which account a session belongs to.
    """
    env = (os.environ.get('CLAUDE_CONFIG_DIR') or '').strip()
    override = env or load_settings().get('claude_config_dir', '')
    if override:
        return os.path.expanduser(os.path.expandvars(override))
    return os.path.join(_USERPROFILE, '.claude')


config_dir        = get_config_dir()
projects_dir      = os.path.join(config_dir, 'projects')
last_session_file = os.path.join(projects_dir, 'last-session.json')
global_claude_md  = os.path.join(config_dir, 'CLAUDE.md')


def global_claude_md_for(cfgdir=None):
    """The global CLAUDE.md of ONE account — the active one when cfgdir is None.

    Same shape as hooks.settings_path_for, and for the same reason: the module
    attribute stays the cfgdir=None answer because the test harness redirects
    writes by monkeypatching it.
    """
    return os.path.join(cfgdir, 'CLAUDE.md') if cfgdir else global_claude_md


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


def account_env(cfgdir=None):
    """Environment for a `claude` invocation that must act on ONE account.

    `CLAUDE_CONFIG_DIR` is Claude Code's own account selector and it is honoured
    by the management subcommands too (`claude plugin …`, `claude mcp …`), not
    only by a session — verified against `plugin marketplace list`, which reads a
    different account's `known_marketplaces.json` per value.

    Popping `ANTHROPIC_API_KEY` is the non-obvious half: a key set in the
    environment shadows the account login, so the CLI would authenticate as the
    key's owner no matter which config dir it read.
    """
    env = os.environ.copy()
    env['CLAUDE_CONFIG_DIR'] = resolve_config_dir(cfgdir)
    env.pop('ANTHROPIC_API_KEY', None)
    return env


def resolve_config_dir(cfgdir=None):
    """Expand a config dir. `cfgdir=None` is the caller saying "whichever
    account is active", which is what every pre-fan-out call site meant.

    Same shape as hooks.settings_path_for: the `config_dir` module attribute
    stays the None answer rather than being recomputed here, because it is the
    attribute the test harness monkeypatches to redirect every path into a tmp
    dir. Reading it through the module rather than by value is what makes an
    account switch — and that monkeypatch — actually move.
    """
    if cfgdir:
        return os.path.expanduser(os.path.expandvars(cfgdir))
    return config_dir


# ── executable discovery ────────────────────────────────────

def _editor_candidates():
    if os.name == 'nt':
        return [
            r'C:\Program Files\Notepad++\notepad++.exe',
            r'C:\Program Files (x86)\Notepad++\notepad++.exe',
            os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs',
                         'Notepad++', 'notepad++.exe'),
            shutil.which('notepad++'),
            shutil.which('code'),
            os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'notepad.exe'),
            shutil.which('notepad'),
        ]
    # $VISUAL/$EDITOR first: on POSIX the user has already answered this
    # question, and answering it differently is the wrong kind of opinion.
    return [shutil.which(os.environ.get('VISUAL', '') or os.environ.get('EDITOR', '')),
            shutil.which('code'), shutil.which('gedit'), shutil.which('kate'),
            shutil.which('nano'), shutil.which('vim'), shutil.which('vi')]


def find_editor():
    """Best available text editor. Settings override first, then a per-platform
    table."""
    override = load_settings().get('editor', '')
    if override and os.path.exists(override):
        return override
    for exe in _editor_candidates():
        if exe and os.path.exists(exe):
            return exe
    return None


def open_in_editor(path):
    """Open path in the best available editor. Returns True if launched."""
    editor = find_editor()
    if not editor:
        return False
    return _spawn_editor(editor, path)


def _spawn_editor(exe, path):
    """The one place an editor process is started.

    The window is asked NOT to take the foreground. claudectl opens an editor
    as a side effect of a screen the user is already looking at, so stealing
    focus interrupts them — and during a test run it interrupts whatever else
    they were doing. Windows honours this through STARTUPINFO
    (SW_SHOWNOACTIVATE); an app that ignores it is out of our hands.
    """
    import subprocess
    kw = {}
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 4          # SW_SHOWNOACTIVATE
        kw['startupinfo'] = si
    try:
        subprocess.Popen([exe, path], **kw)
        return True
    except Exception:
        return False


def get_claude_exe():
    """Locate the Claude Code binary. Settings override > default install path
    > PATH. None if missing. The install path is the same on every platform;
    only the file extension differs."""
    override = load_settings().get('claude_exe', '')
    if override and os.path.exists(override):
        return override
    exe = 'claude.exe' if os.name == 'nt' else 'claude'
    default = os.path.join(_USERPROFILE, '.local', 'bin', exe)
    if os.path.exists(default):
        return default
    for name in (exe, 'claude'):
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

# Named palettes, derived from the authored hex tables in themes.py — that
# module is the single source of truth for both UIs. Each entry maps the
# switchable C_* entries; missing keys keep the default.
THEMES = {name: _themes.ansi_palette(pal)
          for name, pal in _themes.PALETTES.items()}
THEME_NAMES = list(THEMES)


def theme_label(name):
    """Human display name for a theme key ('catppuccin-latte' → 'Catppuccin Latte')."""
    pal = _themes.PALETTES.get(name)
    return pal['label'] if pal else name


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

# `ultracode` is last on purpose: it is not a sixth model effort level but a
# Claude Code setting that sends xhigh AND has Claude orchestrate a dynamic
# workflow per substantive task. It is accepted by --effort (v2.1.203+) and by
# nothing else — NOT by the `effortLevel` setting, NOT by CLAUDE_CODE_EFFORT_LEVEL.
EFFORTS       = ['',        'low', 'medium', 'high', 'xhigh', 'max', 'ultracode']
EFFORT_LABELS = ['default', 'low', 'medium', 'high', 'xhigh', 'max', 'ultracode']
#: values --effort accepts that the effortLevel SETTING does not (ccsettings
#: enforces this — a rejected value is a startup error the user sees instead of
#: their session)
EFFORT_LAUNCH_ONLY = {'max', 'ultracode'}
# Full model ids — claude.exe rejects bare version strings like 'sonnet-4-6'.
#
# This pair is the FLOOR, not the roster. `models()` below answers with the live
# catalogue when there is one (see models.py) and falls back to exactly this,
# so an empty cache, a dead network or a logged-out account still fills every
# picker. Do NOT read these two directly from a picker — read `models()`, or a
# new model release cannot reach the screen and a pinned one can be silently
# erased. `test_no_picker_reads_the_bundled_table_directly` enforces that.
MODELS        = ['', 'claude-haiku-4-5', 'claude-sonnet-5', 'claude-opus-5', 'claude-fable-5']
MODEL_LABELS  = ['default', 'haiku-4-5', 'sonnet-5', 'opus-5', 'fable-5']
# Permission modes, as Claude Code names them. Two entries need explaining:
#
#   ''         pass no --permission-mode at all. This is NOT "manual" — on a
#              Pro/Max/Team plan Claude Code's own built-in starting mode is
#              `auto`, so omitting the flag inherits whatever it decides.
#   'default'  the config value for the mode Claude Code labels MANUAL. It is
#              a real value that must be passed to force manual, which is why
#              it is a separate entry and why ''s label is no longer 'default'.
#
# `auto` runs every action past a classifier model instead of past the user.
# It is unavailable on some models/plans, and Claude Code then starts the
# session in manual SILENTLY — see AUTO_UNSUPPORTED_MODELS / effective_perm.
PERMS         = ['',                'auto', 'default', 'plan', 'acceptEdits', 'bypassPermissions', 'dontAsk']
PERM_LABELS   = ['account default', 'auto', 'manual',  'plan', 'acceptEdits', 'bypassPermissions', 'dontAsk']
PERM_RISKY    = {'bypassPermissions', 'dontAsk'}   # shown with warning tint
#: one line per mode, same job PERM_LABELS cannot do — surfaced by the TUI
#: launch picker and the GUI exactly like EFFORT_PROFILES already is.
PERM_PROFILES = {
    '':                  "inherit Claude Code's own default",
    'auto':              'hands-off — a classifier reviews each action',
    'default':           'manual — asks before edits, commands, network',
    'plan':              'read-only until you approve a plan',
    'acceptEdits':       'file edits + common fs commands, no prompt',
    'bypassPermissions': 'skips every check — containers/VMs only',
    'dontAsk':           'only pre-approved tools; denies the rest',
}
#: Models the auto-mode classifier does not support on ANY provider. Selecting
#: `auto` with one of these does not error — Claude Code just starts the session
#: in manual, so claudectl declines to pass the flag rather than show a mode the
#: session is not in. Haiku and the claude-3 family are the documented cases.
AUTO_UNSUPPORTED_MODELS = {'claude-haiku-4-5'}
# launch-economy: cap thinking tokens (MAX_THINKING_TOKENS env) to cut cost on
# routine work; '' = leave the model's default budget alone.
THINKING_CAPS   = ['',        '4000', '8000', '16000', '32000']
THINKING_LABELS = ['default', '4k', '8k', '16k', '32k']

# ── cost estimation ($ per MTok; substring-matched on message.model) ──
COST_PER_MTOK = {
    'fable-5':    {'in': 10.0, 'out': 50.0},
    'opus-5':     {'in': 5.0,  'out': 25.0},
    'opus-4':     {'in': 5.0,  'out': 25.0},   # legacy transcripts (4.5-4.8)
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
# July-2026 benchmarks (Haiku 73 / Sonnet-5 85; Opus 5 and Fable top the
# roster with no published SWE score -> '—'). speed labels per Anthropic
# (Haiku fastest, Sonnet Fast, Opus Moderate, Fable slow/deep).
MODEL_PROFILES = {
    'claude-haiku-4-5': {'cap': 2, 'swe': 73, 'speed': 'fast', 'best_for': 'bulk, simple edits, subagents'},
    'claude-sonnet-5':  {'cap': 4, 'swe': 85, 'speed': 'fast', 'best_for': 'default coding (~90% of tasks)'},
    'claude-opus-5':    {'cap': 5, 'swe': None, 'speed': 'med', 'best_for': 'deep refactor, hard debugging'},
    'claude-fable-5':   {'cap': 5, 'swe': None, 'speed': 'slow', 'best_for': 'hardest, longest-horizon work'},
}
EFFORT_PROFILES = {
    '':       'account default',
    'low':    'simple / subagents / cheap',
    'medium': 'balanced — sweet spot',
    'high':   'complex work, thorough',
    'xhigh':  'best for coding & agentic',
    'max':    'maximum depth, priciest',
    'ultracode': 'xhigh + a planned workflow per task',
}
# task-based quick-start presets: (name, description, opts-fields)
LAUNCH_PRESETS = [
    ('Recommended',   'everyday coding — best balance',
     {'model': 'claude-sonnet-5', 'effort': 'high'}),
    ('Cheap & fast',  'simple / bulk work, lowest cost',
     {'model': 'claude-sonnet-5', 'effort': 'low', 'subagent_model': 'claude-haiku-4-5'}),
    ('Deep reasoning', 'hard refactor, accuracy-critical',
     {'model': 'claude-opus-5', 'effort': 'xhigh'}),
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


def models(*extra):
    """(ids, labels) for the launch pickers — the live catalogue, then the floor.

    Derived on every call and never cached in a module constant. `MODELS` was
    precisely the "constant derived from mutable state" this codebase has been
    bitten by three times, and half its readers import it BY VALUE — rebinding
    it would reach none of them. The read is cheap: `models.roster()` is a
    pure disk load and never fetches, so a picker may call this per render.

    `extra` ids are appended when the catalogue does not list them, and that is
    load-bearing rather than tidiness. The launch picker resolves the saved
    model to a cursor index and writes `ids[idx]` back on save, so a pin that
    Anthropic has since retired would resolve to 0 (= account default) and be
    ERASED on the next save. It stays in the list, and `models.notices()` is
    what tells the user it is gone.
    """
    from . import models as _m
    try:
        roster = _m.roster()
    except Exception:
        roster = []                    # a catalogue read must never break launch
    if roster:
        ids = [''] + [r['id'] for r in roster]
        labels = ['default'] + [r['label'] for r in roster]
    else:
        ids, labels = list(MODELS), list(MODEL_LABELS)
    for mid in extra:
        mid = (mid or '').strip()
        if mid and mid not in ids:
            ids.append(mid)
            labels.append(mid[7:] if mid.startswith('claude-') else mid)
    return ids, labels


def model_label(model):
    """The short label for one id ('claude-sonnet-5' -> 'sonnet-5')."""
    ids, labels = models(model)
    return labels[ids.index(model)] if model in ids else (model or 'default')


def profile(model):
    """The curated editorial for a model, matched by id then by FAMILY, or None.

    The API owns the facts (context window, efforts, release date); this table
    owns cost rank, capability rank, speed and the `best_for` prose, none of
    which /v1/models returns. Family fallback is what stops the first release
    of a new generation from landing in the picker with every column blank:
    `claude-sonnet-6` inherits sonnet's editorial until someone profiles it.
    """
    if not model:
        return None
    prof = MODEL_PROFILES.get(model)
    if prof:
        return prof
    from . import models as _m
    fam = _m.family(model)
    if not fam:
        return None
    for mid, p in MODEL_PROFILES.items():
        if _m.family(mid) == fam:
            return p
    return None


def cap_bar(model):
    """'▪' capability bar (1-5); '' for account-default/unknown."""
    prof = profile(model)
    return '▪' * (prof['cap'] if prof else 0)


def swe_str(model):
    """'85%' SWE-bench score, or '—' when unknown/account-default.

    A family-inherited score is deliberately NOT shown: a benchmark number is a
    measurement of one model, and attributing sonnet-5's 85% to sonnet-6 would
    be inventing data. Rank and prose generalise across a family; a score does
    not."""
    prof = MODEL_PROFILES.get(model)
    if not prof or prof.get('swe') is None:
        return '—'
    return f"{prof['swe']}%"


def model_card_rows():
    """[(model_id, label, cost_bar, cap_bar, best_for, swe_str)] for the roster.

    A model the catalogue lists but nobody has profiled gets a row with empty
    bars rather than no row at all — visible-and-unprofiled beats absent, which
    is the whole reason the live catalogue is worth having.
    """
    rows = []
    ids, labels = models()
    for mid, label in zip(ids, labels):
        if not mid:
            continue
        prof = profile(mid)
        rows.append((mid, label, cost_bar(mid), cap_bar(mid),
                     (prof or {}).get('best_for', ''), swe_str(mid)))
    return rows


def advise(model, effort):
    """Dynamic launch advisor. Returns (level, message) where level is
    'ok' | 'tip' | 'warn'; names a better model/effort when the pick is
    sub-optimal. Grounded in July-2026 cost/quality data."""
    eff = effort or ''
    ei = EFFORTS.index(eff) if eff in EFFORTS else 0    # 0 default,1 low,2 med,3 high,4 xhigh,5 max
    if not profile(model):
        return ('tip', 'Pick a model — Sonnet 5 · high is the recommended default.')
    if model == 'claude-opus-5' and ei in (1, 2):
        return ('tip', 'Opus is underused at this effort — Sonnet 5 · high gives ~similar quality at ~60% less cost.')
    if model == 'claude-sonnet-5' and ei >= 4:
        return ('warn', 'Sonnet at xhigh burns heavy reasoning tokens — can cost more than Opus 5 · high for similar quality. Use Opus · high or Sonnet · high.')
    if model == 'claude-fable-5' and ei < 4:
        return ('tip', 'Fable is the priciest tier — Opus 5 · xhigh handles almost everything at half the cost.')
    if model == 'claude-haiku-4-5' and ei >= 3:
        return ('warn', "Haiku isn't built for deep reasoning — switch to Sonnet 5 for hard tasks.")
    good = {
        'claude-haiku-4-5': 'Cheapest & fastest — great for bulk, simple edits, and subagents.',
        'claude-sonnet-5':  'Best default — ~90% of coding at Opus quality; high ≈ Opus low.',
        'claude-opus-5':    'Top accuracy tier — deep refactor & hard debugging; xhigh is the coding sweet spot.',
        'claude-fable-5':   'Maximum capability for the hardest, longest-horizon work.',
    }
    return ('ok', good.get(model, ''))


def otel_env(s=None):
    """Claude Code's OpenTelemetry export vars, or {} when it is off.

    Claude Code emits metrics and events over OTLP when
    CLAUDE_CODE_ENABLE_TELEMETRY=1 and the standard OTEL_* variables are set.
    claudectl already assembles the launch environment per project and account,
    so wiring it here means one toggle covers every session it starts.

    PRIVACY, because it is the first thing anyone asks: prompt CONTENT is not
    exported. Claude Code records prompt length only, unless
    OTEL_LOG_USER_PROMPTS=1 — which is deliberately not settable from here. If
    someone wants that they can put it in their own environment, having decided
    to; it is not a checkbox claudectl should offer casually.

    Pure mapping, no I/O — same contract as omniroute_env, so tests can call it.
    """
    s = load_settings() if s is None else s
    endpoint = (s.get('otel_endpoint') or '').strip()
    if not s.get('otel_enabled') or not endpoint:
        return {}
    proto = (s.get('otel_protocol') or 'http/protobuf').strip()
    env = {
        'CLAUDE_CODE_ENABLE_TELEMETRY': '1',
        'OTEL_EXPORTER_OTLP_ENDPOINT': endpoint,
        'OTEL_EXPORTER_OTLP_PROTOCOL': proto,
        'OTEL_METRICS_EXPORTER': 'otlp',
        'OTEL_LOGS_EXPORTER': 'otlp',
    }
    headers = (s.get('otel_headers') or '').strip()
    if headers:                      # e.g. "Authorization=Bearer xyz"
        env['OTEL_EXPORTER_OTLP_HEADERS'] = headers
    return env


def provider_upstream(s):
    """Where the provider actually lives, as far as anything downstream of the
    failover proxy is concerned.

    With a translating gateway configured that is the gateway, not the real
    backend: the gateway IS the thing that speaks the Anthropic Messages API,
    and the OpenAI-shaped host behind it cannot answer `claude` directly. Two
    readers need this answer (provider_env and failover's upstream resolution),
    which is exactly why it is a function and not a line copied into both.

    Chain when everything is on:
        claude -> failover (retries a dead model) -> gateway (translates) -> host
    """
    if s.get('gateway_kind'):
        return 'http://127.0.0.1:%d' % int(s.get('gateway_port') or 20130)
    return s.get('provider_base_url') or ''


def provider_env(s=None, model=None):
    """{} when the session runs on Anthropic direct; else the environment that
    points an interactive ``claude`` launch at the configured alternate backend
    — OmniRoute, a local Ollama/vLLM/llama.cpp server, OpenRouter's Anthropic
    endpoint, or a remote host.  Used for the execution half of Plan→Execute and
    for standalone routed sessions (see main.py, plan_execute.py).

    Returns ``{}`` when no provider is configured.  Pass *model* to force the
    provider env even when ``provider_exec_model`` is unset (the GUI
    plan-execute modal's ``via='provider'`` path uses this).

    Beyond the base URL and token this also disables three things that cannot
    work off Anthropic's own infrastructure — see docs/providers.md for why each
    one is unfixable rather than merely unimplemented."""
    s = load_settings() if s is None else s
    if not s.get('provider_exec_model') and not model:
        return {}
    # Kept a PURE mapping here (no I/O, no spawning, no raising); starting a
    # daemon belongs where prepare_launch's ensure_running already governs
    # launch failure.
    _url = provider_upstream(s)
    if [m for m in (s.get('failover_models') or []) if str(m or '').strip()]:
        _url = 'http://127.0.0.1:%d' % int(s.get('failover_port') or 20129)
    env = {
        'ANTHROPIC_BASE_URL': _url,
        'ANTHROPIC_AUTH_TOKEN': s.get('provider_api_key') or '',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
        # Extended thinking is Anthropic-only in practice, on EVERY provider
        # kind including an Anthropic-shaped local server: a thinking block
        # carries a signature that must round-trip byte-for-byte to the
        # infrastructure that minted it, so a backend that did not mint it
        # cannot produce one. Claude Code sends thinking:{"type":"adaptive"}
        # unconditionally for 4.6+ models and an upstream that does not know
        # the field answers 400 — i.e. the whole session fails, not just the
        # thinking. Off by default, opt back in per launch once verified.
        'CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING': '1',
        # NOTE: CLAUDE_CODE_SUBAGENT_MODEL was previously set to
        # 'claude-sonnet-5' here, but a routed backend can't resolve bare
        # Anthropic model ids — agent calls go through the same proxy and
        # fail with "Ambiguous model".  Agents inherit the session's own
        # model instead, which guarantees routing works.  The model: field in
        # agent .md files is stripped by sync_project_agents(routed=True) for
        # the same reason.
    }
    if s.get('provider_tool_search'):
        # Claude Code turns MCP tool search off on any non-first-party base
        # URL. Re-enabling it only works if the upstream forwards
        # tool_reference blocks, so it is the user's assertion, not ours.
        env['ENABLE_TOOL_SEARCH'] = 'true'
    return {k: v for k, v in env.items() if v}


# Ordered stops on the cost/quality frontier for the GUI's single-slider
# model picker — deliberately curated to the advisor's 'good' combos (not
# every advise()=='ok' pairing) so each stop is a genuine step up in
# capability/cost, cheapest to most powerful. A bad combo (Sonnet·xhigh,
# Opus·low, …) simply isn't reachable from this control.
MODEL_EFFORT_FRONTIER = [
    ('claude-haiku-4-5', 'low'),
    ('claude-sonnet-5',  'medium'),
    ('claude-sonnet-5',  'high'),
    ('claude-opus-5',    'high'),
    ('claude-opus-5',    'xhigh'),
    ('claude-fable-5',   'xhigh'),
    ('claude-fable-5',   'max'),
]


def frontier_rows():
    """[(model, effort, label, cost_bar, swe_str, note)] for each frontier
    stop, cheap→max-power, for the GUI's single frontier slider."""
    rows = []
    for mid, eff in MODEL_EFFORT_FRONTIER:
        _level, note = advise(mid, eff)
        rows.append((mid, eff, model_label(mid),
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
