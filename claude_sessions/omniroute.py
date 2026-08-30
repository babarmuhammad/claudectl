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


def prepare_launch(model, s=None, ctx_bytes=0):
    """THE seam every routed launch goes through: bring the backend up, prove
    the model is real, and return the env overrides for a ``claude`` launch.

    Raises ``RuntimeError`` when a backend cannot be reached and ``ValueError``
    for a model the backend does not serve.  Both are raised BEFORE the session
    opens, deliberately: otherwise the terminal launches fine and only fails
    once `claude` itself tries the model, deep inside a new console with no
    path back to the setting that was wrong.

    Branches on ``provider_kind``:

    * ``omniroute`` — auto-start the npm daemon and validate against the live
      ``/v1/models`` catalogue (``auto/coding`` passes unvalidated; OmniRoute
      resolves it server-side).
    * ``generic``  — any already-running Anthropic-shaped server. There is no
      catalogue to validate against, so the model id is taken on trust and only
      reachability is checked. Do NOT auto-start anything here: the whole point
      of the kind is that the user already runs the server.

    *ctx_bytes* is the CLAUDE.md + rules + plan payload the session will carry;
    passing it enables the small-context advisory (returned, not raised — it is
    never a reason to block a launch).

    Returns ``(env, warning)``. The env is ready to ``env.update()``; callers
    must merge it on top of a full ``os.environ.copy()`` so ``CLAUDE_CONFIG_DIR``
    and the rest of the account env survive into the child."""
    from .config import load_settings, provider_env
    s = load_settings() if s is None else s
    kind = s.get('provider_kind') or ''
    base_url = s.get('provider_base_url', '')
    api_key = s.get('provider_api_key', '')
    if kind == 'omniroute':
        ok, msg = ensure_running(base_url)
        if not ok:
            raise RuntimeError(f'OmniRoute: {msg}')
    elif not s.get('gateway_kind') and not is_reachable(base_url, api_key):
        # With a gateway in front, `base_url` names an OpenAI-shaped host that
        # cannot answer /v1/models — probing it would fail every time and block
        # a working setup. The gateway's own startup check covers that hop.
        raise RuntimeError(f'Provider not reachable at {base_url or "(unset)"}')
    if s.get('gateway_kind'):
        from . import gateway
        gok, gmsg = gateway.ensure_running(s)
        if not gok:
            raise RuntimeError(f'Gateway: {gmsg}')
    # provider_env() has already pointed ANTHROPIC_BASE_URL at claudectl's own
    # failover proxy when candidates are configured, so it must actually be up —
    # fail the launch rather than hand claude a dead base URL.
    from . import failover
    if failover.enabled(s):
        fok, fmsg = failover.ensure_running(s)
        if not fok:
            raise RuntimeError(f'Failover proxy: {fmsg}')
    env = {k: v for k, v in provider_env(s, model=model or '_').items() if v}
    if kind == 'omniroute' and model and model != AUTO_MODEL:
        available = [mid for mid, _lbl in list_models(base_url, api_key)]
        if available and model not in available:
            raise ValueError(f"Model '{model}' is no longer available. "
                             f"Choose from: {available}")
    return env, context_warning(s, ctx_bytes)


def context_warning(s, ctx_bytes):
    """Advisory string, or '' — never a launch blocker.

    Claude Code's own system prompt is 10k+ tokens BEFORE any project context,
    which is why the floor is added rather than measuring the payload alone: a
    default Ollama context of 4096 is already over budget with an empty repo,
    and a check that only weighed CLAUDE.md would call that fine."""
    if not ctx_bytes:
        return ''
    est = 10000 + ctx_bytes // 4
    window = int(s.get('provider_context_tokens') or 0)
    if window:
        if est > window * 0.6:
            return (f"~{est // 1000}k tokens of context against a "
                    f"{window // 1000}k window — the model may degrade")
    elif est > 18000:
        return (f"~{est // 1000}k tokens of context — if this is a local model, "
                "raise its context length (num_ctx on Ollama)")
    return ''


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


def health(base_url, api_key=''):
    """Live per-provider health over HTTP — the source that actually works on
    this platform (`omniroute providers list --json` crashes; see module doc, and
    it returns [] here even with 5 providers connected).

    {'providers': [{name, state, failures, last_failure}], 'lockouts': [...],
     'summary': {...}}.  state: CLOSED = healthy, HALF_OPEN = recovering,
    OPEN = tripped."""
    try:
        d = _get(base_url, '/api/monitoring/health', api_key)
    except Exception:
        return {'providers': [], 'lockouts': [], 'summary': {}}
    ph = (d or {}).get('providerHealth') or {}
    providers = [{'name': k, 'state': (v or {}).get('state', 'UNKNOWN'),
                  'failures': (v or {}).get('failures', 0),
                  'last_failure': (v or {}).get('lastFailure') or ''}
                 for k, v in ph.items()]
    providers.sort(key=lambda p: (p['state'] != 'CLOSED', p['name']))
    lockouts = [{'provider': l.get('provider', ''), 'model': l.get('model', ''),
                 'reason': l.get('reason', ''), 'remaining_ms': l.get('remainingMs') or 0}
                for l in ((d or {}).get('lockouts') or [])]
    return {'providers': providers, 'lockouts': lockouts,
            'summary': (d or {}).get('providerSummary') or {}}


def catalog(base_url, api_key=''):
    """Full /v1/models entries (id, owned_by, context_length, capabilities).
    list_models() keeps its (id, label) shape for its existing callers."""
    try:
        d = _get(base_url, '/v1/models', api_key)
    except Exception:
        return []
    return [m for m in (d or {}).get('data', []) if m.get('id')]


def usable_models(base_url, api_key=''):
    """({'id','label','provider','state','context'} candidates, auto_ids, excluded).

    Narrows /v1/models — which lists every routable id regardless of whether a
    provider backing it is connected — to the ones that could plausibly run a
    session. Each entry's own ``owned_by`` names its provider; do NOT reconstruct
    that from an id-prefix table, which mislabels every provider not in the table.

    Only provable exclusions are applied:
      * circuit breaker OPEN — the provider is tripped;
      * an active quota lockout on that model;
      * no ``context_length`` — image-gen, TTS, ASR and embedding entries. They
        also report ``tool_calling: true``, so capabilities alone can't spot them;
      * ``tool_calling`` false — Claude Code cannot run without tools.

    Deliberately NOT excluded: providers missing from providerHealth. That set is
    "providers OmniRoute has attempted", not "providers configured" — it grows as
    requests are made (confirmed live: it went 5 -> 9 purely from probing, adding
    openrouter/gemini/groq/opencode as CLEAN/CLOSED entries), while
    providerSummary.configuredCount stayed at 5. Treating absence as
    "not configured" would be reading a signal that isn't there.

    So this returns *candidates*, not proof. The only authoritative test is
    sending a real request — see probe_models().
    """
    entries, h = fetch_both(base_url, api_key)
    return classify_models(entries, h)


def fetch_both(base_url, api_key=''):
    """(catalog, health) fetched CONCURRENTLY. Each OmniRoute HTTP round trip
    costs ~2s on a loaded instance, so serial fetches add up fast enough to trip
    gui.py's slow-handler warning."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        c = ex.submit(catalog, base_url, api_key)
        h = ex.submit(health, base_url, api_key)
        return c.result(), h.result()


def classify_models(entries, h):
    """Pure split of catalog entries into (candidates, autos, excluded) given a
    health() dict — separated from usable_models so a caller holding both
    payloads never refetches them. See usable_models for the rules and why
    'missing from providerHealth' is deliberately not one of them."""
    ph = {p['name']: p for p in (h or {}).get('providers', [])}
    locked = {lk['model'] for lk in (h or {}).get('lockouts', []) if lk['model']}
    usable, autos, excluded = [], [], {}
    for m in entries or []:
        mid, owner = m['id'], (m.get('owned_by') or '')
        if owner == 'combo' or mid.startswith('auto/'):
            autos.append(mid)
            continue
        caps = m.get('capabilities') or {}
        ctx = m.get('context_length')
        state = (ph.get(owner) or {}).get('state', 'UNKNOWN')
        if state == 'OPEN':
            excluded.setdefault('%s — provider circuit open' % owner, []).append(mid)
        elif mid in locked or (m.get('root') or '') in locked:
            excluded.setdefault('%s — quota lockout' % owner, []).append(mid)
        elif not ctx:
            excluded.setdefault('%s — not a chat model' % owner, []).append(mid)
        elif not caps.get('tool_calling'):
            excluded.setdefault('%s — no tool support' % owner, []).append(mid)
        else:
            usable.append({'id': mid, 'label': m.get('name') or mid,
                           'provider': owner, 'state': state, 'context': ctx})
    usable.sort(key=lambda u: (u['state'] != 'CLOSED', u['provider'], u['id']))
    return usable, autos, excluded


def classify_failure(detail):
    """Failure class from an upstream error string. A timeout is NOT a verdict on
    the model -- it means our own budget expired, usually because several probes
    queued behind each other inside OmniRoute (one /v1/models call alone costs
    ~2s on a loaded instance). Reporting it as 'failed' is what makes the same
    model look broken in one run and fine in the next."""
    d = (detail or '').lower()
    if 'timed out' in d or 'timeout' in d:
        return 'timeout'
    if '410' in d:
        return 'gone'            # provider retired the id -- permanent
    if 'limit' in d or '429' in d or 'quota' in d:
        return 'limited'         # free-tier budget spent -- resets on a timer
    if '403' in d or '401' in d or 'permission' in d:
        return 'auth'            # key not authorized for that provider
    return 'error'


_DEAD_TTL = 7 * 24 * 3600          # 'gone'/'auth' are permanent; re-check weekly


def dead_path():
    import os
    from .config import settings_file
    return os.path.join(os.path.dirname(settings_file), 'omniroute-dead.json')


_ALIVE_TTL = 6 * 3600              # a recent success is a good ordering hint


def _load_cache():
    from . import jsonstore
    return jsonstore.load(dead_path(), expect=dict)


def load_dead():
    """{id: status} for models that failed permanently (410 gone / 403 auth).
    Re-probing these is the bulk of a slow verify run and cannot change the
    answer until the provider or key changes."""
    import time as _t
    now = _t.time()
    return {k: v.get('status') for k, v in _load_cache().items()
            if isinstance(v, dict) and v.get('status') in ('gone', 'auth')
            and now - (v.get('ts') or 0) < _DEAD_TTL}


def load_alive():
    """Ids that answered recently — probed first, so a verify run that stops
    early stops on models already known to work."""
    import time as _t
    now = _t.time()
    return [k for k, v in _load_cache().items()
            if isinstance(v, dict) and v.get('status') == 'works'
            and now - (v.get('ts') or 0) < _ALIVE_TTL]


def order_fairly(ids, alive=None):
    """Known-good first, then ROUND-ROBIN across providers.

    Without this the order is whatever the catalog sort produced — alphabetical by
    provider — so one slow or broken provider monopolises the whole probe budget
    and providers later in the alphabet are never reached. Measured here: probing
    in catalog order spent 77s on gemini/groq/nvidia/oc and left every one of the
    12 working `openrouter` models unprobed. Round-robin touches each provider
    within the first pass instead."""
    alive = set(alive or [])
    head = [i for i in ids if i in alive]
    rest = [i for i in ids if i not in alive]
    buckets = {}
    for i in rest:
        buckets.setdefault(i.split('/')[0] if '/' in i else '', []).append(i)
    order, queues = [], list(buckets.values())
    while queues:
        for q in list(queues):
            if q:
                order.append(q.pop(0))
            if not q:
                queues.remove(q)
    return head + order


def save_dead(results, clear=False):
    import os
    import time as _t
    p = dead_path()
    cur = {} if clear else {}
    if not clear and os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                cur = json.load(f) or {}
        except Exception:
            cur = {}
    for r in results or []:
        if r.get('ok'):
            cur[r['id']] = {'status': 'works', 'ts': _t.time()}
        elif r.get('status') in ('gone', 'auth'):
            cur[r['id']] = {'status': r['status'], 'ts': _t.time()}
        elif r.get('status') == 'timeout':
            cur.pop(r['id'], None)         # never a verdict; don't let it stick
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cur, f, indent=1)
    except Exception:
        pass


def record_result(mid, ok, detail=''):
    """Learn a model's real state from a live session turn.

    This is free: the request was going to happen anyway. probe_models() spends
    actual free-tier quota to predict the same thing — confirmed the hard way,
    repeated probe runs pushed this instance's OpenRouter free models from
    working into 'Key limit exceeded'. So the proxy teaching the cache is the
    cheap path and probing is the fallback.

    Writes only when the status actually changes, since this runs per turn."""
    if not mid:
        return
    status = 'works' if ok else classify_failure(detail)
    if status in ('timeout', 'error'):
        return                      # neither is a verdict about the model
    cur = _load_cache()
    if (cur.get(mid) or {}).get('status') == status:
        return
    save_dead([{'id': mid, 'ok': bool(ok), 'status': status}])


def probe_models(base_url, ids, api_key='', timeout=20, workers=5,
                 want=0, budget=75, skip=None):
    """[{'id','ok','status','served','detail'}] — one real /v1/messages request
    per model. The only signal that cannot lie: either a model answers or it does
    not; the catalog and health endpoint only predict.

    Bounded, because an unbounded verify run is unusable: probing every candidate
    at a 20s timeout costs minutes.
      * ``skip``  — {id: status} of known-permanent failures, reported without a
        request (see load_dead). Pass {} to force a full re-probe.
      * ``want``  — stop once this many models have answered. A failover list only
        needs a handful, and candidates arrive best-first.
      * ``budget``— total wall-clock ceiling across every probe and retry.
    Timeouts are retried once at a doubled budget (a timeout is our limit
    expiring, not a model fault) but only while budget and need remain.
    """
    import concurrent.futures
    import time as _t

    def _one(mid, per):
        ok, served, msg = test_live(base_url, mid, api_key, timeout=per)
        return {'id': mid, 'ok': bool(ok), 'served': served or '',
                'detail': msg or '',
                'status': 'works' if ok else classify_failure(msg)}

    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return []
    skip = load_dead() if skip is None else skip
    ids = order_fairly(ids, load_alive())

    results, pending = [], []
    for mid in ids:
        if mid in skip:
            results.append({'id': mid, 'ok': False, 'status': skip[mid],
                            'served': '', 'detail': 'known %s — not re-probed' % skip[mid]})
        else:
            pending.append(mid)

    t0 = _t.time()
    n_ok = 0
    timeouts = []
    step = max(1, workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=step) as ex:
        for i in range(0, len(pending), step):
            if _t.time() - t0 > budget or (want and n_ok >= want):
                for mid in pending[i:]:
                    results.append({'id': mid, 'ok': False, 'status': 'skipped',
                                    'served': '', 'detail': 'not probed — enough found'
                                    if want and n_ok >= want else 'not probed — time budget'})
                break
            for r in ex.map(lambda m: _one(m, timeout), pending[i:i + step]):
                results.append(r)
                if r['ok']:
                    n_ok += 1
                elif r['status'] == 'timeout':
                    timeouts.append(r)

    for r in timeouts:
        if _t.time() - t0 > budget or (want and n_ok >= want):
            break
        again = _one(r['id'], timeout * 2)
        if again['ok'] or again['status'] != 'timeout':
            again['detail'] = (again['detail'] or '') + ' (on retry)'
            results[results.index(r)] = again
            if again['ok']:
                n_ok += 1

    by_id = {r['id']: r for r in results}
    return [by_id[i] for i in ids if i in by_id]


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
        from .proc import no_window_flags
        subprocess.run([exe, 'serve', '--daemon'], capture_output=True,
                       text=True, encoding='utf-8', errors='ignore', timeout=15,
                       creationflags=no_window_flags)
    except Exception as e:
        return False, f'could not start OmniRoute: {e}'
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_reachable(base_url, timeout=2):
            return True, 'started'
        time.sleep(1)
    return False, 'OmniRoute did not come up in time — check it manually'


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
        from .proc import no_window_flags
        r = subprocess.run([exe, *args], capture_output=True, text=True,
                           encoding='utf-8', errors='ignore', timeout=timeout,
                           creationflags=no_window_flags)
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


def test_live(base_url, model=None, api_key='', timeout=30):
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
        with urllib.request.urlopen(req, timeout=timeout) as r:
            used = r.headers.get('x-omniroute-model', model)
            provider = r.headers.get('x-omniroute-provider', '')
            return True, used, f'routed to {used}' + (f' via {provider}' if provider else '')
    except urllib.error.HTTPError as e:
        return False, '', f'HTTP {e.code}: {e.read().decode("utf-8", "replace")[:200]}'
    except Exception as e:
        return False, '', str(e)
