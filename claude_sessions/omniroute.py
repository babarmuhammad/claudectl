"""OmniRoute client — free/cheap-tier model execution backend.

OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT, diegosouzapw) is a
self-hosted local proxy that speaks the Anthropic Messages API natively.
Pointing `claude`'s ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN at it is enough
for real Claude Code sessions to run through it for execution work, while
planning stays on the real Anthropic API/expensive model (see
plan_execute.py).

IMPORTANT, confirmed against a live instance (v3.8.48) — do NOT re-add a
"connect free providers automatically" feature without re-checking this:
there is no zero-signup/keyless provider tier. `omniroute providers
available` lists exactly 6 providers (OpenAI, Anthropic, Google AI,
OpenRouter, Groq, Mistral), every one needs the USER's own API key. The big
model list on /v1/models is a static ~257-entry catalog of routable model
ids — none of them work until a provider is actually connected
(providerSummary.configuredCount starts at 0).

ALSO CONFIRMED (reproduced twice each) — do NOT try to automate ADDING a
provider via the CLI without re-checking this first: on this install
(v3.8.48, Windows), both documented paths are broken upstream:
  `omniroute keys add <provider> <key> [--stdin]` -> HTTP 404 + a native
  libuv assertion crash (src/win/async.c).
  `omniroute setup --add-provider --provider X --api-key Y --non-interactive`
  -> "Provider API key is required" even though --api-key/OMNIROUTE_API_KEY
  were both supplied correctly.
Adding a provider is dashboard-only (localhost:20128) for now — not by
design choice, by upstream bug. `omniroute providers list --json` and
`omniroute providers test <id> --json` DO work cleanly (verified) and are
what cli_connections()/cli_test_connection() below use for READ-ONLY status.

Thin urllib + CLI-shellout client, same pattern as usage.py's OAuth usage
fetch — no new dependency.
"""

import json
import shutil
import subprocess
import time
import urllib.request
import urllib.error

# OmniRoute's own dynamic per-request router (docs/routing/AUTO-COMBO.md) —
# passing this as the model id makes OmniRoute pick the best currently-
# healthy free model by a 12-factor score (health/quota/cost/latency/task
# fit/...) and transparently swap to the next-best one on failure/exhaustion
# via its circuit-breaker (resilience.mjs) — entirely server-side, invisible
# to the `claude` client. No claudectl-side ranking or retry logic needed;
# this IS "automatically choose best model, fall back when it runs out."
AUTO_MODEL = 'auto/coding'


def _get(base_url, path, api_key, timeout=5):
    req = urllib.request.Request((base_url or '').rstrip('/') + path)
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def is_reachable(base_url, api_key='', timeout=3):
    try:
        _get(base_url, '/v1/models', api_key, timeout=timeout)
        return True
    except Exception:
        return False


def list_models(base_url, api_key=''):
    """[(id, label)] from OmniRoute's /v1/models, or [] if unreachable."""
    try:
        data = _get(base_url, '/v1/models', api_key)
    except Exception:
        return []
    out = []
    for m in (data or {}).get('data', []):
        mid = m.get('id')
        if mid:
            out.append((mid, m.get('name') or mid))
    return out


def ensure_running(base_url, timeout=25):
    """Make sure the local OmniRoute proxy is up, auto-starting it as a
    detached background daemon if it isn't — so a Plan→Execute run never
    needs the user to have a terminal open. Uses OmniRoute's own `serve
    --daemon` (confirmed in bin/cli/commands/serve.mjs: spawns detached,
    server.unref()s, writes a PID file, returns immediately) rather than a
    foreground process claudectl would have to babysit in a console window.

    Returns (ok, message). Never raises.
    """
    if is_reachable(base_url, timeout=2):
        return True, 'already running'
    exe = shutil.which('omniroute')
    if not exe:
        return False, 'OmniRoute not installed — run: npm install -g omniroute'
    try:
        subprocess.run([exe, 'serve', '--daemon'], capture_output=True,
                       text=True, timeout=15)
    except Exception as e:
        return False, f'could not start OmniRoute: {e}'
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_reachable(base_url, timeout=2):
            return True, 'started'
        time.sleep(1)
    return False, 'OmniRoute did not come up in time — check it manually'


def provider_status(base_url):
    """{'catalog', 'configured', 'active'} counts from OmniRoute's own
    unauthenticated health endpoint — the real signal for "is anything
    actually usable", since /v1/models lists the full routable catalog
    regardless of whether any provider backing it is connected. All zero on
    a fresh install; 'configured' > 0 once the user's added a key via
    OmniRoute's own dashboard."""
    try:
        data = _get(base_url, '/api/monitoring/health', '')
    except Exception:
        return {'catalog': 0, 'configured': 0, 'active': 0}
    s = (data or {}).get('providerSummary') or {}
    return {'catalog': s.get('catalogCount', 0), 'configured': s.get('configuredCount', 0),
            'active': s.get('activeCount', 0)}


def _cli(args, timeout=15):
    """Run `omniroute <args>` and parse its --json stdout. None if the
    binary is missing, the call errors, or the output isn't valid JSON
    (some subcommands crash outright on this platform — see module doc).

    The CLI prints ANSI-colored "Loaded env from ..." log lines to stdout
    BEFORE the JSON payload (confirmed) — every --json response here is a
    JSON *object*, so strip everything before the first '{'. (NOT also
    checking for '[': the ANSI escape codes themselves contain a literal
    '[' — e.g. '\\x1b[2m' — which sorts earlier than the real JSON and
    corrupts the parse if it's treated as a candidate start marker.)"""
    exe = shutil.which('omniroute')
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    start = r.stdout.find('{')
    if start == -1:
        return None
    try:
        return json.loads(r.stdout[start:])
    except Exception:
        return None


def cli_connections():
    """[{id, provider, name, status, error}] — real per-connection
    PASS/FAIL/error state via `omniroute providers list --json`, not just a
    count. [] if the CLI is missing/unavailable."""
    data = _cli(['providers', 'list', '--json'])
    out = []
    for p in (data or {}).get('providers', []):
        out.append({'id': p.get('id', ''), 'provider': p.get('provider', ''),
                    'name': p.get('name', ''), 'status': p.get('testStatus', 'unknown'),
                    'error': p.get('lastError', '')})
    return out


def cli_test_connection(id_or_name):
    """Re-test one connection via `omniroute providers test`. CONFIRMED
    UNRELIABLE — it reported 'error: no API key configured' for a
    genuinely no-auth OpenCode connection that was serving real,
    successful responses the whole time. Keep this only as a secondary/
    informational signal; test_live() below is the authoritative check.
    Returns (ok, message)."""
    data = _cli(['providers', 'test', id_or_name, '--json'], timeout=20)
    if data is None:
        return False, 'omniroute CLI not available'
    return bool(data.get('valid')), (data.get('error') or 'ok')


def test_live(base_url, model=None, api_key=''):
    """The check that actually matters: send one real request through
    /v1/messages (what the exec `claude` session will actually call) and
    see if it comes back. OmniRoute's own connection-level health check
    (cli_test_connection) can be stale/wrong; this can't lie the same way —
    either a real model answers or it doesn't. Returns (ok, model_used, message).
    """
    model = model or AUTO_MODEL
    body = json.dumps({'model': model, 'max_tokens': 8,
                       'messages': [{'role': 'user', 'content': 'hi'}]}).encode('utf-8')
    req = urllib.request.Request(
        (base_url or '').rstrip('/') + '/v1/messages', data=body, method='POST',
        headers={'Content-Type': 'application/json'})
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            used = r.headers.get('x-omniroute-model', model)
            provider = r.headers.get('x-omniroute-provider', '')
            return True, used, f'routed to {used}' + (f' via {provider}' if provider else '')
    except urllib.error.HTTPError as e:
        return False, '', f'HTTP {e.code}: {e.read().decode("utf-8", "replace")[:200]}'
    except Exception as e:
        return False, '', str(e)
