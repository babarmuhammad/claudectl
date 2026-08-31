"""Headless smoke test for the GUI: boot the real PAGE against stub API data and
inspect what actually rendered.

The string-matching tests in tests/ prove the code is *shaped* right; this proves
it *runs* — instruments mount and paint, readouts carry values, the frame loop
parks when nothing is happening, every page renders without a JS error, and the
narrow-window layout re-fits.

Cannot verify the QtWebEngine flicker itself: a headless/GPU-less box won't
composite to a capturable buffer, so screen-scrape probes see a static frame
(CLAUDE.md). That needs real hardware.

    py -3 tools/smoke_gui.py
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from claude_sessions import gui                       # noqa: E402
from claude_sessions.gui_html import PAGE, vendor_asset  # noqa: E402
from claude_sessions import themes as _TH        # noqa: E402
from claude_sessions import config as _TH_CFG    # noqa: E402

PORT = 8793

STATE = {
    'projects': [{'name': 'acme-api', 'path': '/demo/acme-api', 'encoded': 'demo-acme-api',
                  'accounts': ['default', 'teamA'], 'primary_cfgdir': '',
                  'auto_memory': True, 'last_active': '2m'},
                 {'name': 'acme-web', 'path': '/demo/acme-web', 'encoded': 'demo-acme-web',
                  'accounts': ['teamA'], 'primary_cfgdir': 'w',
                  'auto_memory': False, 'last_active': '1d'}],
    'accounts': [{'name': 'default', 'dir': '', 'active': True},
                 {'name': 'teamA', 'dir': 'w', 'active': False}],
    'recent': [{'project': 'acme-api', 'path': '/demo/acme-api', 'encoded': 'demo-acme-api',
                'sid': 's1', 'name': 'a session', 'age': '2m', 'cfgdir': ''}],
    # The permission list is the REAL one, for the same reason the presets below
    # are: it grew from four modes to six, and a one-item ['d'] stub audits a
    # control that cannot wrap, cannot overflow and cannot be wrong.
    # ...and the effort list is real for a sharper version of the same reason:
    # the tick row under the slider is laid out per stop, and a two-stop stub
    # cannot put a label in the wrong place. It shipped six hand-typed labels
    # against a seven-stop slider — the thumb pointed at HIGH while the readout
    # said xhigh — and every audit passed, because they were auditing two stops.
    'options': {'efforts': list(_TH_CFG.EFFORTS), 'models': ['opus', 'sonnet'],
                'model_labels': ['Opus', 'Sonnet'],
                'perms': list(_TH_CFG.PERMS), 'perm_labels': list(_TH_CFG.PERM_LABELS),
                'perm_profiles': dict(_TH_CFG.PERM_PROFILES),
                'perm_notes': {p: {m: list(_TH_CFG.perm_note(p, m))
                                   for m in ('', 'opus', 'sonnet')}
                               for p in _TH_CFG.PERMS},
                'thinking': [''], 'thinking_labels': [''],
                'frontier': [['opus', 'high', 'Opus', '$$', '70', 'note']],
                # real presets, so the launch modal actually renders its cards.
                # Without them tools/shot_gui.py audits an empty modal — which is
                # how a pill radius that turned every preset into an ellipse got
                # past it.
                'presets': [[n, d, f] for n, d, f in _TH_CFG.LAUNCH_PRESETS]},
    'defaults': {'effort': '', 'model': '', 'perm': '', 'max_thinking': '',
                 'subagent_model': ''},
    'ui_mode': 'gui', 'gui_shell': 'auto', 'theme': 'neon', 'motion': 'full',
    'stage': 'cinematic',
    'themes': gui.theme_palettes(), 'skin': '',
    'skins': {n: dict(v) for n, v in _TH.SKINS.items()},
    'worlds': {n: dict(v) for n, v in _TH.WORLDS.items()},
    'classic_skins': list(_TH.CLASSIC_SKINS),
    'world': '',
    'plan_model': '', 'exec_model': '', 'extract_model': '',
    'omniroute_base_url': '', 'omniroute_has_key': False,
    'omniroute_exec_model': '', 'failover_models': [], 'failover_port': 20129,
    'failover_quiet': False,
}
_NOW = time.time()
DASH = {
    # by_account is what the quota ring's arcs are built from — three accounts
    # so the segmented path is actually exercised. Tokens, never percentages:
    # each account's quota % is a share of its OWN window and they do not add.
    'today': {'tokens': 412000, 'sessions': 7, 'cost': 28.0,
              'by_account': {'default': 240000, 'teamA': 130000, 'teamB': 42000},
              'omni_tokens': 60000},
    'days': 30, 'generated_at': _NOW,
    'week': [{'tokens': 300000}] * 7,
    # a finished job and a failed one, so the Activity drawer has all three
    # groups to render. Nothing RUNNING: the parking assertions below depend on
    # an idle workspace, and the live path is exercised explicitly further down.
    'jobs': [{'id': 'j1', 'kind': 'Memory build', 'status': 'done', 'elapsed': 42,
              'started': _NOW - 400, 'ended': _NOW - 358, 'error': '',
              'last': 'wrote 18 entities'},
             {'id': 'j2', 'kind': 'Code review', 'status': 'error', 'elapsed': 9,
              'started': _NOW - 900, 'ended': _NOW - 891,
              'error': 'claude.exe not found', 'last': ''}],
    'wiring': {'ok': 1, 'total': 2, 'accounts': [
        {'account': 'default', 'dir': '', 'hooks': 4, 'statusline': True,
         'statusline_hidden': False, 'mode': 'auto'},
        {'account': 'teamA', 'dir': 'w', 'hooks': 0, 'statusline': True,
         'statusline_hidden': True, 'mode': ''}]},
    # An IDLE workspace: the parking assertions below all assume nothing is
    # running, and two permanently-live sessions would contradict them — the
    # stage is supposed to stay awake while sessions are live. The live path is
    # exercised explicitly further down instead.
    'live': {'total': 0, 'by_account': {}, 'window': 600},
    'hours': [0, 1, 0, 3, 2, 0, 0, 0, 1, 0, 0, 0, 0, 2, 4, 1, 0, 0, 0, 0, 1, 0, 0, 2],
    'mcp': [{'name': 'ide', 'running': True}, {'name': 'asana', 'running': False}],
    'failover': {'running': True, 'port': 20129},
    'recent': [{'project': 'acme-api', 'title': 'a session', 'msgs': 12, 'sid': 's1',
                'age': '2m', 'path': '/demo/acme-api', 'encoded': 'demo-acme-api',
                'cfgdir': '', 'account': 'default', 'omni': True}],
    'breakdown': {
        'days': [{'date': '2026-08-%02d' % (i + 1), 'tokens': 100000 + i * 9000,
                  'cost': i * 0.4, 'omni_tokens': i * 3000,
                  'accounts': {'default': 100000 + i * 9000}} for i in range(14)],
        'accounts': [{'account': 'default'}],
        'projects': [
            {'name': 'acme-api', 'enc': 'demo-acme-api', 'tokens': 900000, 'cost': 4.2,
             'age': '2m', 'mtime': _NOW, 'accounts': ['default', 'teamA'],
             'omni': True, 'sparkline': [1, 4, 2, 7, 3, 9, 5]},
            {'name': 'acme-web', 'enc': 'demo-acme-web', 'tokens': 300000, 'cost': 1.1,
             'age': '1d', 'mtime': _NOW - 90000, 'accounts': ['teamA'],
             'sparkline': [2, 1, 3]}],
        'totals': {'omni_tokens': 120000, 'omni_saved': 7.5}},
}
PLAN = {'accounts': [{'account': 'default', 'email': 'demo@example.com', 'plan': 'max',
                      'status': 'ok',
                      'windows': [{'label': 'session', 'pct': 62, 'resets': 'in 3h'},
                                  {'label': 'weekly', 'pct': 88, 'resets': 'Fri'}]}]}
ROUTES = {
    '/api/state': STATE, '/api/dashboard': DASH, '/api/usage/plan': PLAN,
    '/api/memory/active': {'active': ['/demo/acme-api']},
    '/api/search-index': {'rows': []},
    '/api/mcp': {'servers': [{'name': 'ide', 'status': 'ok'},
                             {'name': 'asana', 'status': 'down'}]},
    '/api/usage/daily': {'days': [{'day': 'd%d' % i, 'tokens': i * 1000,
                                   'tok_fmt': '%dk' % i, 'cost': i * .1}
                                  for i in range(14)]},
    '/api/usage/projects': {'projects': []},
    '/api/accounts': {'accounts': [
        {'name': 'default', 'resolved': '~/.claude', 'active': True, 'dir': ''},
        {'name': 'teamA', 'resolved': '~/.claude-teamA', 'active': False, 'dir': 'w'}]},
    # Claude Code's own state. Stubbed with CONTENT, not empties: the settings
    # editor is a grid whose column count comes from the account list, and an
    # empty one renders nothing for the overflow audit to look at.
    '/api/client/usage': {
        'skills': [{'name': 'artifact-design', 'count': 7, 'last_used': '18d'},
                   {'name': 'claude-api', 'count': 4, 'last_used': '6d'}],
        'plugins': [{'name': 'caveman@caveman', 'count': 2473, 'last_used': '86m'}],
        'agents': [{'name': 'bg', 'count': 0, 'last_used': '59d'}]},
    '/api/background-agents': {
        'daemon': {'running': False, 'workers': [], 'recognised': True,
                   'updated': '8d'},
        'teams': {'teams': [], 'tasks': [], 'recognised': False}},
    '/api/disk': {'bytes': 795_000_000, 'accounts': [
        {'account': 'default', 'dir': '~/.claude', 'bytes': 569_000_000,
         'stores': [{'name': 'projects', 'bytes': 495_000_000, 'files': 1065,
                     'oldest_days': 120},
                    {'name': 'file-history', 'bytes': 72_000_000, 'files': 2253,
                     'oldest_days': 90}]},
        {'account': 'teamA', 'dir': '~/.claude-teamA', 'bytes': 127_000_000,
         'stores': [{'name': 'projects', 'bytes': 115_000_000, 'files': 69,
                     'oldest_days': 40}]}]},
    # auto mode: two accounts that DISAGREE about the starting mode, plus a
    # denial group — the two states the card exists to make visible
    '/api/automode': {
        'accounts': [{'name': 'default', 'dir': '', 'mode': 'auto',
                      'environment': ['$defaults', 'Source control: github.com/acme']},
                     {'name': 'teamA', 'dir': 'w', 'mode': '', 'environment': []}],
        'modes': list(_TH_CFG.PERMS), 'mode_labels': list(_TH_CFG.PERM_LABELS),
        'profiles': dict(_TH_CFG.PERM_PROFILES),
        'denials': [{'key': 'Bash:git', 'tool': 'Bash', 'count': 3, 'last': 0,
                     'reason': 'Blocked by classifier',
                     'samples': ['git push --force origin main']}]},
    '/api/automode/config': {'ok': True, 'rules': {'allow': ['Test Artifacts: …']},
                             'error': ''},
    '/api/cc-settings': {
        'groups': ['Model & reasoning', 'Context & memory', 'Advanced'],
        'schema': {
            'model': {'kind': 'str', 'choices': [], 'group': 'Model & reasoning',
                      'help': 'Default model id for new sessions'},
            'effortLevel': {'kind': 'enum',
                            'choices': ['low', 'medium', 'high', 'xhigh', 'max'],
                            'group': 'Model & reasoning',
                            'help': 'Default reasoning effort'},
            'alwaysThinkingEnabled': {'kind': 'bool', 'choices': [],
                                      'group': 'Model & reasoning',
                                      'help': 'Think before every response'},
            'autoCompactWindow': {'kind': 'int', 'choices': [],
                                  'group': 'Context & memory',
                                  'help': 'Tokens of context to keep when compacting'},
            'env': {'kind': 'json', 'choices': [], 'group': 'Advanced',
                    'help': 'Environment variables for every session'}},
        'accounts': [
            {'name': 'default', 'dir': '~/.claude',
             'values': {'effortLevel': 'high', 'model': 'claude-sonnet-5'}},
            {'name': 'teamA', 'dir': '~/.claude-teamA',
             'values': {'effortLevel': 'max'}}]},
    '/api/prompt-history': {'prompts': [
        {'text': 'fix the parser', 'project': '/demo/acme-api', 'sid': 's1', 'ts': 1}]},
    # A workspace with no sessions in it screenshots as an advert for nothing,
    # and the session list is the first thing the app is for.
    '/api/sessions': {'sessions': [
        {'sid': 'a1b2c3d4', 'title': 'retry storm on the payments upstream',
         'preview': 'the gateway retried every 200ms and stampeded',
         'age': '12m', 'mtime': _NOW - 720, 'count': 84, 'account': 'default',
         'cfgdir': '', 'tokens': '412k', 'omni': False},
        {'sid': 'b2c3d4e5', 'title': 'move invoice totals to integer cents',
         'preview': 'a float total drifted by a cent across the rollup',
         'age': '3h', 'mtime': _NOW - 10800, 'count': 61, 'account': 'default',
         'cfgdir': '', 'tokens': '288k', 'omni': False},
        {'sid': 'c3d4e5f6', 'title': 'split the checkout handler',
         'preview': 'one function did validation, pricing and dispatch',
         'age': '1d', 'mtime': _NOW - 86400, 'count': 137, 'account': 'teamA',
         'cfgdir': 'w', 'tokens': '910k', 'omni': True},
        {'sid': 'd4e5f6a7', 'title': 'add the migration gate to deploy',
         'preview': 'a deploy went out ahead of its schema change',
         'age': '2d', 'mtime': _NOW - 172800, 'count': 42, 'account': 'default',
         'cfgdir': '', 'tokens': '156k', 'omni': False},
        {'sid': 'e5f6a7b8', 'title': 'cache the search index warm-up',
         'preview': 'cold start took 9s on every deploy',
         'age': '4d', 'mtime': _NOW - 345600, 'count': 25, 'account': 'teamA',
         'cfgdir': 'w', 'tokens': '77k', 'omni': True}]},
    # The memory tab is the headline feature, so the demo workspace has a
    # memory: an empty one screenshots as an advert for nothing.
    '/api/memory/state': {
        'generated_at': '2026-08-12T09:15:00Z',
        'n_entities': 148, 'n_lessons': 9, 'n_pending': 2, 'n_unscanned': 1,
        'hook_on': True, 'rules_on': True,
        'est': {'coverage': 82, 'modules': 11, 'tokens': 232, 'budget': 250}},
    '/api/lessons': {'lessons': [
        {'id': 'l1', 'status': 'approved', 'confidence': 0.9,
         'name': 'Retries need a jittered backoff',
         'summary': 'The gateway retried on a fixed 200ms and stampeded the '
                    'upstream; every retry path takes jitter now.'},
        {'id': 'l2', 'status': 'pinned', 'confidence': 0.8,
         'name': 'Money is integer cents, never float',
         'summary': 'A float total drifted by a cent across the invoice '
                    'rollup; the ledger is integer cents end to end.'},
        {'id': 'l3', 'status': 'pending', 'confidence': 0.6,
         'name': 'Migrations run before the deploy gate',
         'summary': 'Observed twice: a deploy went out ahead of its schema '
                    'change and the API 500d until the migration landed.'}]},
    '/api/worklog': {'on': True, 'installed': True, 'entries': [
        {'when': '2h', 'summary': 'split the checkout handler, added retries',
         'files': ['api/checkout.py', 'api/retry.py']},
        {'when': '1d', 'summary': 'moved totals to integer cents',
         'files': ['billing/ledger.py']}]},
    '/api/workspace-status': {'score': 86, 'label': 'healthy', 'lines': [
        'memory 2h old · 148 entities · 11 modules',
        'CLAUDE.md 232 tok of a 250 budget',
        'git: main · clean']},
    '/api/agents': {'categories': []}, '/api/skills': {'project': [], 'templates': []},
    '/api/hooks': {'hooks': [], 'templates': []},
    '/api/omniroute/status': {'ok': False}, '/api/failover/status': {'running': False},
    '/api/memory/auto-list': {'projects': []},
    '/api/plugins': {'dir': 'C:/x/plugins', 'marketplaces': [
        {'name': 'official', 'source': 'github', 'repo': 'anthropics/claude-plugins',
         'path': 'C:/x/mkt'}],
        'plugins': [{'name': 'demo', 'key': 'demo@official', 'marketplace': 'official',
                     'version': '1.0', 'missing': False,
                     'provides': {'skill': ['a', 'b'], 'hook': ['h']}}]},
    '/api/plugins/provenance': {'provenance': {'skill': {'a': 'demo@official'}}},
    # one plugin behind its marketplace and a Claude Code two releases behind:
    # the update buttons only exist in that state, so the stub has to be in it.
    # Same rule for the other two subjects on this route — claudectl with an
    # upgrade waiting, and a model catalogue holding a retired pin, because the
    # warning rows are the only part of those cards worth auditing.
    '/api/versions': {
        'claudectl': {'installed': '1.6.0', 'latest': '1.7.0', 'mode': 'pip',
                      'update': True, 'current': False, 'error': ''},
        'models': {'count': 4, 'families': 4, 'live': True, 'age': 3600,
                   'fetched': 1, 'error': '',
                   'notices': ['default_model is set to claude-opus-4-1, '
                               'which Anthropic no longer offers']},
        'claude': {'installed': '2.1.239', 'mode': 'native', 'channel': 'latest',
                   'latest': '2.1.241', 'stable': '2.1.231', 'behind': 2,
                   'current': False, 'target': '2.1.241',
                   'versions': ['2.1.241', '2.1.240', '2.1.239'],
                   'local': ['2.1.239', '2.1.232'], 'error': '', 'fetched': 0},
        'plugins': [{'key': 'demo@official', 'name': 'demo', 'marketplace': 'official',
                     'version': '1.0', 'sha': '', 'scope': 'user',
                     'available': '1.1', 'ref': '', 'outdated': True}]},
    # a PARENT of repos, one carrying a submodule — the shape the flat board
    # could not render at all, so the stub has to be the hard case
    '/api/worktrees': {'repo': True, 'multi': True, 'root': 'D:/repos', 'repos': [
        {'path': 'D:/repos/ws', 'name': 'ws', 'kind': 'repo', 'branch': 'develop',
         'head': 'abc', 'dirty': 3, 'ahead': 0, 'behind': 2,
         'sublabel': 'submodules',
         'worktrees': [
             {'path': 'D:/repos/ws', 'name': 'ws', 'branch': 'develop',
              'head': 'abc', 'main': True, 'dirty': 3, 'ahead': 0, 'behind': 2,
              'session': None},
             {'path': 'D:/wt-a', 'name': 'wt-a', 'branch': 'feat', 'head': 'def',
              'main': False, 'dirty': 1, 'ahead': 2, 'behind': 0,
              'session': {'sid': 'deadbeef', 'title': 'refactor',
                          'account': 'teamA', 'msgs': 12, 'age': 30,
                          'live': True}}],
         'children': [
             {'path': 'D:/repos/ws/core', 'name': 'core', 'kind': 'submodule',
              'branch': 'develop', 'head': 'f00', 'dirty': 0, 'ahead': 0,
              'behind': 0, 'sublabel': 'nested repos', 'children': [],
              'worktrees': [{'path': 'D:/repos/ws/core', 'name': 'core',
                             'branch': 'develop', 'head': 'f00', 'main': True,
                             'dirty': 0, 'ahead': 0, 'behind': 0,
                             'session': None}]}]},
        {'path': 'D:/repos/solo', 'name': 'solo', 'kind': 'repo', 'branch': 'main',
         'head': 'aaa', 'dirty': 0, 'ahead': 0, 'behind': 0,
         'sublabel': 'nested repos', 'children': [],
         'worktrees': [{'path': 'D:/repos/solo', 'name': 'solo', 'branch': 'main',
                        'head': 'aaa', 'main': True, 'dirty': 0, 'ahead': 0,
                        'behind': 0, 'session': None}]}]},
    '/api/output-styles': {'active': 'default', 'styles': [
        {'name': 'default', 'description': 'As it ships.', 'scope': 'built-in',
         'builtin': True, 'active': True, 'lines': 0},
        {'name': 'Reviewer', 'description': 'Reviews only.', 'scope': 'project',
         'builtin': False, 'active': False, 'lines': 12}]},
    '/api/statusline': {'installed': False, 'preview': 'Opus 5 - memory 4m',
                        'command': 'py -m claude_sessions statusline'},
    # the Tools tab's two async cards. Without them it audits two spinners —
    # which is how a wall-of-text "since last session" block stayed unnoticed.
    '/api/health': {'issues': [{'severity': 'warn', 'message': 'CLAUDE.md is heavy',
                                'hint': 'trim prose; the memory digest is already micro'}],
                    'bash': [{'command': c, 'count': n} for c, n in
                             (('cd', 272), ('py', 93), ('grep', 53), ('ls', 26),
                              ('git', 23), ('tasklist', 19), ('sed', 18))]},
    '/api/brief': {
        'suggestions': [{'tag': 'fix', 'text': 'recurring issue: ' + 'long prose ' * 30}],
        'since': {'since': '2026-08-20', 'note': '', 'repos': [
            {'label': 'IKM.IkmVision', 'path': '/demo/a', 'dirty': 7,
             'commits': ['6580438 fix(cmake): select stubs by target architecture',
                         'a5fdea9 Merged PR 869: Fix OCSORT + interpolation']},
            {'label': 'IKM.Platform', 'path': '/demo/b', 'dirty': 2, 'commits': []}]},
        'since_last': ['▸ IKM.IkmVision', '  6580438 fix(cmake)']},
    '/api/checkpoints': {'recognised': True, 'store': True, 'orphans': 1,
                         'files': [{'path': 'D:/x/a.py', 'name': 'a.py',
                                    'versions': [{'v': 1, 'size': 10, 'mtime': 1},
                                                 {'v': 2, 'size': 12, 'mtime': 2}]}]},
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o):
        b = json.dumps(o).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _raw(self, body, ctype):
        self.send_response(200)
        self.send_header('Content-Type', ctype + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/':
            self._raw(PAGE.encode(), 'text/html')
            return
        # the vendored modules, same allowlist the real server uses. Without
        # these the import in index.html fails, `vendor-ready` never fires and
        # the whole stage silently falls back — which the checks below would
        # then report as a stage bug rather than a missing route.
        if p.startswith('/vendor/'):
            got = vendor_asset(p[len('/vendor/'):])
            if got is None:
                self.send_error(404)
                return
            self._raw(*got)
            return
        self._j(ROUTES.get(p, {}))

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(n)
        self._j({'ok': True})


NL = chr(10)


def main():
    from playwright.sync_api import sync_playwright
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    errs = []
    fails = []

    ran = []

    def check(label, ok, detail=''):
        print(('  OK   ' if ok else '  FAIL ') + label + (' — ' + str(detail) if detail else ''))
        ran.append(label)
        if not ok:
            fails.append(label)

    def wait_for(expr, timeout=8000):
        """Poll a JS expression until truthy. These values settle per FRAME, not
        per second — the stage caps its own fps and the chain parks when hidden —
        so `sleep(n) then assert` measures how many frames the machine managed,
        not the behaviour. Three separate flakes here were all that."""
        end = time.time() + timeout / 1000.0
        while time.time() < end:
            if pg.evaluate(expr):
                return True
            pg.wait_for_timeout(120)
        return False

    with sync_playwright() as pw:
        # Headless Chromium has no GPU, so WebGL needs SwiftShader explicitly or
        # every scene falls back to the static gradient and the stage checks
        # below test nothing. --enable-unsafe-swiftshader is required from
        # Chrome 132; harmless before it.
        br = pw.chromium.launch(args=[
            '--use-gl=angle', '--use-angle=swiftshader',
            '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'])
        pg = br.new_page(viewport={'width': 1600, 'height': 1000})
        pg.on('console', lambda m: errs.append((m.type, m.text)) if m.type == 'error' else None)

        # keep the stack: 'Cannot set properties of null' with no frame behind it
        # is a scavenger hunt across 3000 lines
        pg.on('pageerror', lambda e: errs.append(('pageerror', str(e) + '\n' + (e.stack or ''))))
        pg.goto(f'http://127.0.0.1:{PORT}/')
        pg.wait_for_timeout(1800)

        print('\n— dashboard —')
        kinds = pg.evaluate("INST.reg.map(t=>t.kind+':'+t.key)")
        check('instruments mounted', len(kinds) >= 5, kinds)
        paint = pg.evaluate("""INST.reg.map(t=>{
          const c=t.cv.getContext('2d');
          if(!t.cv.width||!t.cv.height)return t.kind+':NOSIZE';
          const d=c.getImageData(0,0,t.cv.width,t.cv.height).data;
          let n=0;for(let i=3;i<d.length;i+=4)if(d[i])n++;
          return t.kind+':'+(n>200?'painted':'BLANK');})""")
        check('every gauge painted', all('painted' in p for p in paint), paint)
        reads = pg.evaluate("[...document.querySelectorAll('.iread b')].map(e=>e.textContent)")
        # a genuine zero is a valid reading (no jobs running); only the '–'
        # placeholder means a gauge never received a feed
        check('readouts left the placeholder', '–' not in reads and '' not in reads, reads)
        # The readout is TOTAL TOKENS today, not a quota percentage. Each
        # account's quota % is a share of its own window, so no sum or average
        # of those five numbers is a meaningful "total" — tokens are the only
        # cross-account aggregate that actually adds up. 240k+130k+42k = 412k.
        check('quota ring reads the additive total', reads and reads[0] == '412.0k', reads[:1])
        segs = pg.evaluate("(INST.feed('quota').segments||[]).length")
        check('quota ring has one arc per account', segs == 3, segs)
        leg = pg.evaluate("[...document.querySelectorAll('#iQuotaLeg span')].map(e=>e.textContent)")
        check('every arc is named in the legend', len(leg) == 3, leg)

        # ── the activity drawer ──
        # It must render from the payload the poll already has and never fetch,
        # so opening it costs a repaint and nothing else.
        # openActivity() is fully synchronous — it renders from the payload the
        # poll already fetched. So the counter is read in the SAME evaluate,
        # with no await in between: anything else (the 10s poll, the heartbeat)
        # cannot slip in, and the claim stays about the drawer rather than about
        # whatever else the page happened to be doing.
        nf = pg.evaluate("""(()=>{
          const of=window.fetch; let n=0;
          window.fetch=function(){n++;return of.apply(this,arguments)};
          try{ openActivity(); } finally { window.fetch=of; }
          return n;})()""")
        check('activity drawer never fetches', nf == 0, nf)
        rows = pg.evaluate("document.querySelectorAll('#actBody .arow3').length")
        check('activity drawer lists the finished jobs', rows == 2, rows)
        sects = pg.evaluate("document.querySelectorAll('#actBody .sect').length")
        check('activity drawer has all three groups', sects == 3, sects)
        pg.evaluate("closeActivity()")
        check('activity drawer closes',
              not pg.evaluate("document.querySelector('#actovl').classList.contains('show')"))
        units = pg.evaluate("[...document.querySelectorAll('.iread i')].map(e=>e.textContent)")
        print('       units:', units)
        foots = pg.evaluate("[...document.querySelectorAll('.ifoot')].map(e=>e.textContent.trim())")
        for f in foots:
            print('       ·', f)
        kpi = pg.evaluate("[...document.querySelectorAll('.kpi .kv2')].map(e=>e.textContent)")
        check('KPI strip tweened', all(k not in ('', '–') for k in kpi), kpi)
        links = pg.evaluate("INST.feed('flow').links.length")
        check('flow map found a shared-account link', links > 0, f'{links} links')
        rows = pg.evaluate("document.querySelectorAll('#dashProjects .hrow').length")
        check('project rows reconciled', rows == 2, f'{rows} rows')

        # Activity reads LIVE SESSIONS across accounts, not claudectl's own jobs.
        # It used to read the latter and so sat at 0 on a busy workspace.
        foot = pg.evaluate("document.querySelector('#iJobsFoot').textContent")
        check('activity says idle when nothing is live', 'idle' in foot, foot)
        pg.evaluate("""refreshDashboard.__t = 1;
          (function(){ const d = window.__DASH || {};
            d.live = {total:3, by_account:{teamA:2, teamB:1}};
            d.hours = [0,1,0,3,2,0,0,0,1,0,0,0,0,2,4,1,0,0,0,0,1,0,0,2];
            window.__DASH = d; })()""")
        live = pg.evaluate("""(()=>{
          const d={live:{total:3,by_account:{teamA:2,teamB:1}},
                   hours:[0,1,0,3,2,0,0,0,1,0,0,0,0,2,4,1,0,0,0,0,1,0,0,2],
                   jobs:[],today:{sessions:9}};
          const live=d.live, nlive=live.total, jobs=0, sess=9;
          const byAcct=Object.entries(live.by_account)
            .sort((a,b)=>b[1]-a[1]).map(([n,c])=>n+(c>1?' '+c:'')).join(' · ');
          return nlive+'|'+byAcct;})()""")
        check('and names every account when they are',
              live == '3|teamA 2 · teamB', live)
        print('\n— the background stage —')
        vend = pg.evaluate("[!!window.THREE, !!window.ANI, !!window.THREE_POST]")
        check('vendored three/anime/postprocessing loaded', vend == [True, True, True], vend)
        check('anime does not start a second rAF chain',
              pg.evaluate("MO.ani && MO.ani.engine.useDefaultMainLoop===false"))
        live = pg.evaluate("[STAGE.ok, STAGE.failed, document.documentElement.classList.contains('stage-on')]")
        check('stage mounted and painted a frame', live == [True, False, True], live)
        check('the static wash is gone once GL is live',
              pg.evaluate("getComputedStyle(document.body,'::before').opacity") == '0')
        # a scene per skin, each one actually building
        bad = pg.evaluate("""(()=>{const out=[];
          const S=ST.skins||{};
          for(const n of Object.keys(ST.worlds||{})){
            try{ST.world=n;applyTheme(ST.theme);
              const want=S[ST.worlds[n].skin].stage;
              if(!STAGE.ok)out.push(n+':dead');
              else if(STAGE.scene!==want)out.push(n+':'+STAGE.scene);
              else if(!document.documentElement.classList.contains('world-'+n))out.push(n+':no class');
            }catch(e){out.push(n+':'+e.message);}}
          ST.world='';
          for(const n of (ST.classic_skins||[])){
            try{ST.skin=n;applyTheme(ST.theme);
              if(!STAGE.ok)out.push(n+':dead');
              else if(STAGE.scene!==S[n].stage)out.push(n+':'+STAGE.scene);
            }catch(e){out.push(n+':'+e.message);}}
          ST.skin='';applyTheme(ST.theme);return out;})()""")
        # A scene object can construct fine while its shader fails to compile —
        # three logs that to the console and renders nothing. Counting console
        # errors across the loop is what actually catches an undeclared uniform.
        pg.wait_for_timeout(400)
        shader = [t for t in errs if 'Shader Error' in t[1] or 'not compiled' in t[1]]
        check('every world and skin builds its own scene', bad == [], bad)
        check('and every shader compiles', not shader,
              shader[0][1].split(chr(10))[0] if shader else '')

        print('\n— …and it is driven by state, not free-running —')
        # The whole justification for bringing a background back. String matching
        # can show the wiring exists; only running it shows the numbers move.
        pg.evaluate("stopDashboard()")          # else it re-feeds energy every 10s
        pg.evaluate("STAGE.energy(0)")
        wait_for("STAGE._E < 0.15")

        def clock_rate(ms=900):
            """Scene seconds advanced per RENDERED second — the clock multiplier
            itself. Measuring _T against wall time instead makes this a
            benchmark of the software rasteriser: headless SwiftShader drops
            frames, MO clamps a long dt, and the two windows accumulate
            different amounts of render time, so the ratio came out at 1.5 for
            a clock that had genuinely doubled."""
            t0, w0 = pg.evaluate("STAGE._T"), pg.evaluate("STAGE._Tw")
            pg.wait_for_timeout(ms)
            dt = pg.evaluate("STAGE._T") - t0
            dw = pg.evaluate("STAGE._Tw") - w0
            return (dt / dw) if dw > 0.05 else 0.0

        idle = pg.evaluate("STAGE._E")
        idle_rate = clock_rate()
        check('idle settles to a crawl', idle < 0.15, round(idle, 3))

        pg.evaluate("stageEnergy(4,0)")          # four live sessions = flat out
        wait_for("STAGE._E > 0.7")
        busy = pg.evaluate("STAGE._E")
        busy_rate = clock_rate()
        # A threshold near the asymptote pins how many frames the machine
        # managed inside the wait, not the behaviour. What matters is that
        # energy climbs a long way above idle and heads for its target.
        check('a running job raises energy',
              busy > 0.6 and busy > idle + 0.5, f'idle {idle:.2f} -> busy {busy:.2f}')
        # 0.55 + 1.7E is the clock in stage.js, so these two energies predict the
        # ratio exactly. Assert most of it rather than an invented constant: the
        # old 2.5x was above the maximum the formula can produce and had never
        # run, because the block it lived in was unreachable.
        want = ((0.55 + 1.7 * busy) / (0.55 + 1.7 * idle))
        check('and the scene clock runs proportionally faster',
              busy_rate > idle_rate * (want * 0.8),
              f'{idle_rate:.2f}x idle vs {busy_rate:.2f}x busy (predicted {want:.2f}x)')
        # Burn alone must NOT saturate. Driving liveness off a throughput number
        # is the one-word bug that made the equalizer animate forever after a
        # single token had been spent; the stage must not repeat it.
        pg.evaluate("stageEnergy(0,1)")
        wait_for("Math.abs(STAGE._E - 0.45) < 0.08")
        burn = pg.evaluate("STAGE._E")
        check('burn alone does not saturate it', 0.25 < burn < 0.65, round(burn, 3))

        pg.evaluate("MO.launched(document.querySelector('.main'))")
        pg.wait_for_timeout(120)
        check('launching fires a shockwave', pg.evaluate("STAGE._shock") > 0.5)
        check('which decays away', wait_for("STAGE._shock === 0"),
              pg.evaluate("STAGE._shock"))

        was = pg.evaluate("[STAGE._densTgt, STAGE._T]")
        pg.evaluate("go('settings')")
        pg.wait_for_timeout(200)
        now = pg.evaluate("[STAGE._densTgt, STAGE._T, STAGE._pulse]")
        check('each page tunes the one stage', now[0] < was[0], f'{was[0]} -> {now[0]}')
        check('navigation ripples but never restarts it',
              now[2] > 0 and now[1] >= was[1], now)
        pg.evaluate("go('home');startDashboard()")
        pg.wait_for_timeout(600)

        print('\n— the headline claim: idle renders zero frames —')
        # The stage is the ONE job allowed to stay registered, so turn it off to
        # assert the underlying property: with nothing live, the chain empties.
        pg.evaluate("STAGE.setTier('off')")
        # long enough for the heartbeat's second tick (pollActiveMem runs every
        # other 2500ms tick) as well as for every gauge to settle
        pg.wait_for_timeout(4000)
        pip = pg.evaluate("document.querySelectorAll('#plist .amk.pip').length")
        check('scanning project shows a live pip', pip == 1, f'{pip}')
        parked = pg.evaluate("[MO._raf===null, MO._jobs.size, INST.job===null]")
        check('frame loop parked while idle', parked == [True, 0, True], parked)

        print('\n— and with the stage running, it still stops when unseen —')
        pg.evaluate("STAGE.setTier('cinematic')")
        pg.wait_for_timeout(700)
        check('stage keeps the chain alive while visible',
              pg.evaluate("MO._raf!==null && MO._jobs.size>0"),
              pg.evaluate("MO._jobs.size"))
        # Qt reports a minimized window as visible, which is why this is driven
        # off blur rather than document.hidden alone
        pg.evaluate("setVis(false)")
        pg.wait_for_timeout(400)
        check('blur parks the chain even with the stage on',
              pg.evaluate("MO._raf===null"))
        pg.evaluate("setVis(true)")
        pg.wait_for_timeout(400)
        check('focus resumes it', pg.evaluate("MO._raf!==null"))
        pg.evaluate("MO.set('off')")
        pg.wait_for_timeout(400)
        check('motion:off stops the stage too', pg.evaluate("MO._raf===null"))
        pg.evaluate("MO.set('full');STAGE.setTier('off')")
        pg.wait_for_timeout(300)

        print('\n— a running job keeps the activity gauge alive —')
        # Stop the 10s dashboard poll first. It re-feeds beats:0 from the stub,
        # so whether this check sees a live gauge would otherwise depend on
        # where in the poll cycle the preceding checks happened to land.
        pg.evaluate("stopDashboard()")
        pg.evaluate("INST.set('jobs',{v:.5,beats:2})")
        pg.wait_for_timeout(600)
        alive = pg.evaluate("[MO._raf!==null, INST.job!==null]")
        check('gauge runs while work is running', alive == [True, True], alive)
        pg.evaluate("INST.set('jobs',{v:0,beats:0})")
        pg.wait_for_timeout(1500)
        check('and parks again when it stops',
              pg.evaluate("MO._raf===null"), pg.evaluate("MO._jobs.size"))
        pg.evaluate("startDashboard()")

        print('\n— every page renders —')
        # taken from NAV, not a hardcoded list: the list had already fallen
        # behind by one page, and a page nobody checks is a page nobody knows
        # is broken
        pages = pg.evaluate('NAV.map(n => n[0])') + ['home']
        check('page list came from NAV', len(pages) > 8, pages)
        for p in pages:
            before = len(errs)
            pg.evaluate(f"go('{p}')")
            pg.wait_for_timeout(500)
            check(f'page {p}', len(errs) == before, errs[before:])

        # "no JS error" passes on a card that rendered nothing, and the update
        # buttons only exist when something is actually behind — so assert the
        # state, not just the absence of an exception.
        pg.evaluate("go('plugins')")
        pg.wait_for_timeout(600)
        ver = pg.evaluate(
            "(()=>{const c=document.querySelector('#verCard');return c?{"
            "card:1,behind:/2 releases behind/.test(c.textContent),"
            "latest:/Update to latest/.test(c.textContent),"
            "rollback:/2\\.1\\.232/.test(c.textContent)}:{card:0};})()")
        check('claude code version card', ver.get('card') and ver.get('behind')
              and ver.get('latest') and ver.get('rollback'), ver)
        pupd = pg.evaluate(
            "[...document.querySelectorAll('#pluginCard button')]"
            ".filter(b=>b.textContent.trim()==='Update').length")
        check('an outdated plugin offers an update', pupd == 1, pupd)

        print('\n— project tabs —')
        # Two dispatchers: go() drives the global pages, `TAB=<id>;go('project')`
        # the per-project ones. The page walk above only ever exercised the
        # first, so the worktree board — the largest thing on the project side —
        # had never been rendered by this tool.
        pg.evaluate("openProject(ST.projects[0])")
        pg.wait_for_timeout(700)
        # Derived, like the page list two blocks up. This walked a hardcoded
        # 4-of-9 while carrying a comment about exactly this rot.
        for t in pg.evaluate('TABS.map(t => t[0])'):
            before = len(errs)
            pg.evaluate(f"TAB='{t}';go('project')")
            pg.wait_for_timeout(600)
            check(f'tab {t}', len(errs) == before, errs[before:])

        # "no JS error" passes on an EMPTY card, which is exactly what the old
        # renderer produced against the new API shape — so assert the tree.
        pg.evaluate("TAB='worktrees';go('project')")
        pg.wait_for_timeout(600)
        groups = pg.evaluate("document.querySelectorAll('#content details.rgrp').length")
        check('repos tab groups every repo', groups == 3, groups)
        txt = pg.evaluate("document.querySelector('#content').innerText")
        check('a submodule is nested and labelled',
              'core' in txt and 'submodule' in txt and 'submodules' in txt, txt[:200])
        check('a worktree still shows its session', 'refactor' in txt, txt[:200])

        print('\n— controls, not read-outs —')
        # A toggle you can only READ is the class no route-coverage test can
        # see, because the failure is not an unused route — it is no route at
        # all. So: the memory flags must be real checkboxes.
        pg.evaluate("TAB='memory';go('project')")
        pg.wait_for_timeout(900)
        for cid in ('memHook', 'memRules', 'wlOn', 'autoMem'):
            kind = pg.evaluate(
                f"(()=>{{const e=document.querySelector('#{cid}');"
                f"return e?e.type:'missing';}})()")
            check(f'#{cid} is a checkbox', kind == 'checkbox', kind)
        btype = pg.evaluate(
            "(()=>{const e=document.querySelector('#memBudget');return e?e.type:'missing';})()")
        check('#memBudget is a number input', btype == 'number', btype)

        pg.evaluate("go('settings')")
        pg.wait_for_timeout(900)
        # every settings key the server accepts must resolve to a LIVE control.
        # `editor`, `claude_exe`, `claude_config_dir` and `headless_budget_usd`
        # round-tripped through the API with nothing on the page to set them.
        for cid in ('sEditor', 'sClaudeExe', 'sCfgDir', 'sBudget'):
            ok = pg.evaluate(
                f"(()=>{{const e=document.querySelector('#{cid}');"
                f"return !!e && !e.disabled && e.offsetParent!==null;}})()")
            check(f'#{cid} is a live, enabled control', ok)

        pg.evaluate("go('helpp')")
        pg.wait_for_timeout(700)
        rows = pg.evaluate("document.querySelectorAll('[data-help-row]').length")
        want = pg.evaluate('NAV.length + TABS.length')
        check('the help page is generated from the inventory', rows == want,
              f'{rows} rows vs {want} pages+tabs')
        keys = pg.evaluate(
            "document.querySelector('#content').innerText.includes('Terminal UI keys')")
        check('the help page renders the TUI key table', keys)

        pg.evaluate("go('hooks')")
        pg.wait_for_timeout(900)
        boxes = pg.evaluate(
            "document.querySelectorAll('#content .hrow input[type=checkbox]').length")
        rows = pg.evaluate("document.querySelectorAll('#content .hrow').length")
        check('every hook row can be enabled or disabled', rows == 0 or boxes == rows,
              f'{boxes} controls on {rows} rows')

        pg.evaluate("go('home')")
        pg.wait_for_timeout(500)

        print('\n— motion levels —')
        for lv, want in [('subtle', 'mo-subtle'), ('off', 'mo-off'), ('full', 'mo-beam')]:
            pg.evaluate(f"MO.set('{lv}')")
            pg.wait_for_timeout(200)
            cls = pg.evaluate("document.documentElement.className")
            check(f'motion={lv}', want in cls, cls)
        pg.evaluate("MO.set('off')")
        pg.wait_for_timeout(400)
        check('off stops the loop', pg.evaluate("MO._raf===null"))
        pg.evaluate("MO.set('full')")

        print('\n— running-job banner —')
        pg.evaluate("""(()=>{const J={jid:'x',label:'Building memory',status:'running',
          msgs:[{ok:true,text:'step 1'}],elapsed:12,sub:'12s elapsed',err:'',
          sel:'#jban',host:document.querySelector('#jban'),modal:false};
          JOBS['x']=J;inlineRender(J);})()""")
        pg.wait_for_timeout(300)
        check('banner visible with a travelling border',
              pg.evaluate("!!document.querySelector('#jban .perun.beam')"))
        check('banner label rendered',
              pg.evaluate("document.querySelector('#jban .jlbl').textContent")
              == 'Building memory')

        print('\n— skin signature effects fire once and clean up —')
        # stop the 10s dashboard poll first: it can wake the frame loop mid-check
        # and make a perfectly-parked burst look like a leak
        pg.evaluate("stopDashboard()")
        looks = ([('world', w) for w in pg.evaluate("Object.keys(ST.worlds||{})")]
                 + [('skin', k) for k in pg.evaluate("ST.classic_skins||[]")])
        for kind, sk in looks:
            if kind == 'world':
                pg.evaluate(f"ST.world='{sk}';applyTheme(ST.theme)")
            else:
                pg.evaluate(f"ST.world='';ST.skin='{sk}';applyTheme(ST.theme)")
            # wait for the node, never for a fixed number of milliseconds:
            # applyTheme rebuilds the scene and repaints the page, and bursting
            # before .d-continue is back makes MO.burst return early — which
            # reads as a broken burst rather than as a slow runner
            pg.wait_for_selector('.d-continue', timeout=10000)
            pg.evaluate("MO.burst(document.querySelector('.d-continue'))")
            pg.wait_for_timeout(100)
            mounted_n = pg.evaluate("document.querySelectorAll('.burst').length")
            nodes = pg.evaluate("document.querySelectorAll('.burst i').length")
            # The 1.6s budget is a property of the ANIMATION, so read it off the
            # timeline — asserting it in wall clock measures the rasteriser, the
            # exact mistake CLAUDE.md already records for scene time under
            # SwiftShader. The animations live on the burst's PARTICLES, not on
            # the host: reading only the host returns 0ms and makes the clause
            # pass vacuously, which is the "check that runs nothing" failure
            # this tool has already had once. The wall-clock wait below is only
            # a leak check now, so it can be generous.
            budget = pg.evaluate(
                "Math.max(0,...[...document.querySelectorAll('.burst')]"
                ".flatMap(h=>[h,...h.querySelectorAll('*')])"
                ".flatMap(el=>el.getAnimations().map("
                "a=>a.effect.getComputedTiming().endTime||0)))")
            in_budget = 0 < budget <= 1600
            gone = True
            try:
                pg.wait_for_function(
                    "document.querySelectorAll('.burst').length===0", timeout=8000)
            except Exception:
                gone = False
            pg.wait_for_timeout(500)
            parked = pg.evaluate("MO._raf===null")
            check(f'burst {sk}',
                  mounted_n == 1 and nodes > 0 and in_budget and gone and parked,
                  f'mounted={mounted_n} nodes={nodes} budget={budget:.0f}ms '
                  f'cleaned={gone} parked={parked}')
        pg.evaluate("MO.set('subtle');MO.burst(document.querySelector('.d-continue'))")
        pg.wait_for_timeout(150)
        check('no burst at motion=subtle',
              pg.evaluate("document.querySelectorAll('.burst').length") == 0)
        pg.evaluate("MO.set('full');ST.world='';ST.skin='';applyTheme(ST.theme);startDashboard()")

        # -- the provider card changes shape per backend --
        # The card was OmniRoute-shaped for its whole life: a live catalogue, a
        # provider-health panel and a dashboard button. None of that exists for a
        # server the user runs themselves, and rendering it anyway reported a
        # working Ollama as "0 providers connected".
        print(NL + '-- provider card --')
        pg.evaluate("go('settings')")
        pg.wait_for_timeout(700)
        for cid in ('pvKind', 'gwKind', 'gwUrl', 'gwKey', 'pvCtx', 'pvTools',
                    'pvHeadless', 'orUrl', 'orKey', 'gwRow'):
            check('control #' + cid + ' exists',
                  pg.evaluate("!!document.getElementById('" + cid + "')"))
        kinds = pg.evaluate(
            "[...document.querySelectorAll('#pvKind .chip')].map(c=>c.dataset.v)")
        check('every backend kind is offered',
              kinds == ['', 'generic', 'omniroute'], kinds)
        gws = pg.evaluate(
            "[...document.querySelectorAll('#gwKind .chip')].map(c=>c.dataset.v)")
        check('gateway kinds offered', gws == ['', 'openai'], gws)

        pg.evaluate("""document.querySelector('#pvKind .chip[data-v="generic"]').click()""")
        pg.wait_for_timeout(900)
        check('generic backend gets a free-text model input',
              pg.evaluate("!!document.getElementById('pvModel')"))
        check('generic backend hides the OmniRoute catalogue',
              pg.evaluate("!document.getElementById('sOrPin')"))
        check('OmniRoute-only actions hidden for a generic backend',
              pg.evaluate("[...document.querySelectorAll('.orOnly')]"
                          ".every(e=>e.style.display==='none')"))

        pg.evaluate("""document.querySelector('#pvKind .chip[data-v=""]').click()""")
        pg.wait_for_timeout(900)
        check('Anthropic direct offers no model widget',
              pg.evaluate("!document.getElementById('pvModel') "
                          "&& !document.getElementById('sOrPin')"))

        print('\n— narrow window —')
        pg.set_viewport_size({'width': 700, 'height': 900})
        pg.wait_for_timeout(800)
        cols = pg.evaluate("getComputedStyle(document.querySelector('.app')).gridTemplateColumns")
        check('sidebar collapsed to an icon rail', cols.startswith('64px'), cols)
        sizes = pg.evaluate("INST.reg.map(t=>t.cv.clientWidth+'x'+t.cv.clientHeight)")
        check('gauges re-fitted',
              all(int(s.split('x')[0]) > 20 and int(s.split('x')[1]) > 20 for s in sizes),
              sizes)
        pg.set_viewport_size({'width': 1600, 'height': 1000})

        print('\n— every theme applies —')
        bad = pg.evaluate("""(()=>{const out=[];
          for(const n of Object.keys(ST.themes)){
            try{applyTheme(n);
              const c=getComputedStyle(document.documentElement);
              if(!c.getPropertyValue('--mo-lift').trim())out.push(n+':nolift');
            }catch(e){out.push(n+':'+e.message);}}
          return out;})()""")
        check('all palettes apply with a personality', bad == [], bad)

        print(chr(10) + '— shader attributes —')
        # A vertex shader can declare `attribute float li` while the geometry
        # never sets it: WebGL reads 0 for every vertex, nothing errors, and the
        # object silently collapses. That is how the graph's connecting lines
        # disappeared — every segment pinned to node 0, so zero length. Compare
        # what each shader asks for against what its geometry actually has.
        missing = pg.evaluate(r"""(()=>{
          const out=[];
          for(const w of Object.keys(ST.worlds||{})){
            ST.world=w; applyTheme(ST.theme);
            if(!STAGE._sc) continue;
            STAGE._sc.scene.traverse(o=>{
              if(!o.material||!o.material.vertexShader||!o.geometry)return;
              const want=[...o.material.vertexShader.matchAll(
                /^\s*attribute\s+\w+\s+(\w+)\s*;/gm)].map(m=>m[1]);
              const have=Object.keys(o.geometry.attributes);
              const builtin=['position','normal','uv','color'];
              for(const a of want)
                if(!have.includes(a)&&!builtin.includes(a))
                  out.push(w+':'+o.type+':'+a);
            });
          }
          ST.world=''; applyTheme(ST.theme); return out;})()""")
        check('no shader reads an attribute its geometry never set',
              missing == [], missing)

        br.close()
    srv.shutdown()

    # A suite that runs nothing passes everything. This whole file spent a
    # while as unreachable code after a `return` — one helper defined at the
    # wrong indentation closed the `with` block around it — and it reported
    # "FAILURES: none" every time. A floor on the number of checks executed is
    # the cheapest thing that would have caught it.
    # And a floor that never moves stops being a floor: it rises with the suite.
    FLOOR = 85
    if len(ran) < FLOOR:
        fails.append(f'only {len(ran)} checks ran, expected >= {FLOOR} — '
                     'part of this suite is not executing')

    print('\nJS errors:', errs if errs else 'none')
    print('FAILURES:', fails if fails else 'none')
    return 1 if (fails or errs) else 0


if __name__ == '__main__':
    raise SystemExit(main())
