"""The translating gateway: Anthropic Messages in, OpenAI Chat Completions out.

Two of these cover failure modes that a naive implementation passes anyway and
only breaks in production, so they are written deliberately:

  * the mid-stream stall (not just a slow first token), because a local model
    doing grammar-constrained tool-call decoding goes quiet AFTER real content
    has already started, and Claude Code aborts a quiet stream either way;
  * a resumed session carrying historical `thinking` blocks, because that is a
    request shape you only ever see after switching backends mid-conversation —
    exactly when it is hardest to diagnose.
"""
import io
import json
import threading

from claude_sessions import gateway


# ── request translation ──────────────────────────────────────

def test_the_system_array_is_flattened_not_dropped():
    """Claude Code sends `system` as an ARRAY whose first entry is its own
    attribution block. Dropping it because it is not a string would remove the
    entire system prompt."""
    out = gateway.to_openai_request({
        'model': 'm',
        'system': [{'type': 'text', 'text': 'attribution'},
                   {'type': 'text', 'text': 'you are helpful'}],
        'messages': [{'role': 'user', 'content': 'hi'}]})
    assert out['messages'][0] == {'role': 'system',
                                  'content': 'attribution\nyou are helpful'}
    assert out['messages'][1] == {'role': 'user', 'content': 'hi'}


def test_tool_use_becomes_a_tool_call_and_tool_result_becomes_a_tool_message():
    out = gateway.to_openai_request({'model': 'm', 'messages': [
        {'role': 'assistant', 'content': [
            {'type': 'text', 'text': 'let me look'},
            {'type': 'tool_use', 'id': 'tu_1', 'name': 'Read',
             'input': {'path': 'a.py'}}]},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': 'file body'}]},
    ]})
    asst = out['messages'][0]
    assert asst['tool_calls'][0]['id'] == 'tu_1'
    assert asst['tool_calls'][0]['function']['name'] == 'Read'
    assert json.loads(asst['tool_calls'][0]['function']['arguments']) == {'path': 'a.py'}
    tool = out['messages'][1]
    assert tool == {'role': 'tool', 'tool_call_id': 'tu_1', 'content': 'file body'}


def test_a_tool_result_is_ordered_before_the_user_text_that_follows_it():
    """Upstream pairs a result to its call by position as well as id; emitting
    the user's text first orphans the call."""
    out = gateway.to_openai_request({'model': 'm', 'messages': [
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': 'r'},
            {'type': 'text', 'text': 'now do the next bit'}]}]})
    assert [m['role'] for m in out['messages']] == ['tool', 'user']


def test_historical_thinking_blocks_are_dropped_from_a_resumed_session():
    """A thinking block's signature must round-trip byte-for-byte to the
    infrastructure that minted it. Replaying one at a different backend is the
    "Invalid signature in thinking block" failure, and it shows up on RESUME —
    long after the backend was switched, so it never looks related."""
    out = gateway.to_openai_request({'model': 'm', 'messages': [
        {'role': 'assistant', 'content': [
            {'type': 'thinking', 'text': 'hmm', 'signature': 'sig-from-anthropic'},
            {'type': 'text', 'text': 'the answer'}]}]})
    blob = json.dumps(out)
    assert 'sig-from-anthropic' not in blob
    assert 'hmm' not in blob
    assert out['messages'][0]['content'] == 'the answer'


def test_tools_are_rewritten_into_function_shape():
    out = gateway.to_openai_request({'model': 'm', 'messages': [], 'tools': [
        {'name': 'Read', 'description': 'read a file',
         'input_schema': {'type': 'object', 'properties': {'p': {'type': 'string'}}}}]})
    fn = out['tools'][0]
    assert fn['type'] == 'function'
    assert fn['function']['name'] == 'Read'
    assert fn['function']['parameters']['properties']['p']['type'] == 'string'


def test_cache_control_is_dropped_loudly(capsys):
    """Silently dropping it bills every turn uncached forever with no signal --
    the user sees a cost change they cannot attribute."""
    gateway._warned.clear()
    gateway.to_openai_request({
        'model': 'm', 'system': [{'type': 'text', 'text': 'x',
                                  'cache_control': {'type': 'ephemeral'}}],
        'messages': []})
    assert 'cache_control' in capsys.readouterr().out


# ── response translation ─────────────────────────────────────

def test_a_plain_completion_becomes_an_anthropic_message():
    out = gateway.to_anthropic_response({
        'id': 'x', 'model': 'qwen', 'choices': [
            {'message': {'content': 'hello'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 3}}, 'qwen')
    assert out['type'] == 'message' and out['role'] == 'assistant'
    assert out['content'] == [{'type': 'text', 'text': 'hello'}]
    assert out['stop_reason'] == 'end_turn'
    assert out['usage'] == {'input_tokens': 7, 'output_tokens': 3}


def test_tool_calls_come_back_as_tool_use_with_stop_reason_tool_use():
    out = gateway.to_anthropic_response({'choices': [{'message': {'tool_calls': [
        {'id': 'c1', 'function': {'name': 'Read', 'arguments': '{"p":"a.py"}'}}]},
        'finish_reason': 'tool_calls'}]}, 'm')
    assert out['stop_reason'] == 'tool_use'
    assert out['content'][0] == {'type': 'tool_use', 'id': 'c1', 'name': 'Read',
                                 'input': {'p': 'a.py'}}


def test_malformed_tool_arguments_do_not_take_the_turn_down():
    """Local models emit invalid JSON arguments often enough that raising here
    would make the whole backend unusable. An empty input reaches the tool and
    returns a normal error the model can react to."""
    out = gateway.to_anthropic_response({'choices': [{'message': {'tool_calls': [
        {'id': 'c1', 'function': {'name': 'Read', 'arguments': '{"p": '}}]},
        'finish_reason': 'tool_calls'}]}, 'm')
    assert out['content'][0]['input'] == {}


# ── streaming ────────────────────────────────────────────────

def _events(frames):
    return [f.split(b'\n')[0].removeprefix(b'event: ').decode() for f in frames]


def test_the_stream_emits_the_event_sequence_claude_code_expects():
    tr = gateway.StreamTranslator('m')
    frames = tr.feed({'choices': [{'delta': {'content': 'he'}}]})
    frames += tr.feed({'choices': [{'delta': {'content': 'llo'},
                                    'finish_reason': 'stop'}]})
    frames += tr.finish()
    assert _events(frames) == ['message_start', 'content_block_start',
                               'content_block_delta', 'content_block_delta',
                               'content_block_stop', 'message_delta', 'message_stop']
    text = b''.join(frames)
    assert b'"text":"he"' in text and b'"text":"llo"' in text


def test_streamed_tool_call_fragments_are_concatenated_before_emission():
    """OpenAI splits `arguments` across deltas and each fragment is invalid JSON
    on its own, so a half-parsed argument object is nothing the client can use."""
    tr = gateway.StreamTranslator('m')
    tr.feed({'choices': [{'delta': {'tool_calls': [
        {'index': 0, 'id': 'c1', 'function': {'name': 'Read', 'arguments': '{"p"'}}]}}]})
    tr.feed({'choices': [{'delta': {'tool_calls': [
        {'index': 0, 'function': {'arguments': ':"a.py"}'}}]}}]})
    frames = tr.finish()
    blob = b''.join(frames)
    assert b'"partial_json":"{\\"p\\":\\"a.py\\"}"' in blob
    assert b'"stop_reason":"tool_use"' in blob


def test_an_empty_upstream_stream_still_closes_the_message():
    """A backend that answers with nothing must not leave the client waiting for
    a message_stop that never comes."""
    frames = gateway.StreamTranslator('m').finish()
    assert _events(frames) == ['message_start', 'message_delta', 'message_stop']


def test_iter_sse_stops_at_done_and_skips_unparseable_lines():
    body = io.BytesIO(b'data: {"a":1}\n\n: comment\n\ndata: nonsense\n\n'
                      b'data: {"a":2}\n\ndata: [DONE]\n\ndata: {"a":3}\n\n')
    assert list(gateway.iter_sse(body)) == [{'a': 1}, {'a': 2}]


# ── the keepalive ────────────────────────────────────────────

def test_the_ping_frame_is_a_valid_anthropic_event():
    assert gateway.PING.startswith(b'event: ping')
    assert b'"type": "ping"' in gateway.PING or b'"type":"ping"' in gateway.PING


def test_writes_are_serialized_so_a_ping_cannot_land_inside_a_frame(monkeypatch):
    """The ping thread and the relay loop share one socket. Without a lock a
    ping interleaves mid-frame and corrupts the event the client is parsing --
    which reads as a protocol error, not as a concurrency bug."""
    src = open(gateway.__file__, encoding='utf-8').read()
    stream = src.split('def _stream(')[1]
    assert 'with lock:' in stream
    # every write goes through the one helper, not straight to wfile
    body = stream.split('def write(')[1]
    assert body.count('self.wfile.write') == 1


def test_the_ping_timer_covers_the_whole_stream_not_just_the_first_byte():
    """Scoping the keepalive to the pre-first-byte gap is the obvious
    implementation and misses the stall it exists to prevent: a local model goes
    quiet in the MIDDLE of a turn. The timer is reset by every upstream chunk
    instead of being cancelled after the first."""
    src = open(gateway.__file__, encoding='utf-8').read()
    stream = src.split('def _stream(')[1]
    # the relay loop refreshes the deadline per chunk...
    relay = stream.split('for chunk in iter_sse(')[1]
    assert 'last[0] = time.time()' in relay
    # ...and the ping thread runs until the stream is finished, not until first byte
    assert 'while not done.wait(' in stream
    assert 'done.set()' in stream


# ── the guard and the Anthropic denylist ─────────────────────

def test_the_gateway_refuses_to_target_anthropic():
    """It substitutes the user's own upstream credential into everything it
    forwards. Aiming it at Anthropic would be a claudectl-built request wearing
    the Anthropic client's endpoint -- the exact traffic shape Anthropic has
    taken enforcement action over elsewhere."""
    for bad in ('https://api.anthropic.com', 'https://claude.ai/x',
                'https://foo.anthropic.com/v1'):
        assert 'refusing' in gateway.target_error(bad)
    assert gateway.target_error('http://localhost:1234/v1') == ''


def test_an_unconfigured_target_is_a_refusal_to_start_not_a_broken_session():
    assert gateway.target_error('') != ''
    ok, msg = gateway.ensure_running({'gateway_target_base_url': ''})
    assert not ok and 'no gateway target' in msg


def test_the_two_daemons_do_not_share_a_lock_file_or_a_readiness_marker():
    """A bare port check would trust whichever daemon answered. The marker is
    per-name so failover cannot be mistaken for the gateway, and vice versa."""
    from claude_sessions import failover
    assert gateway._D.lock_path() != failover._D.lock_path()
    assert gateway._D.marker != failover._D.marker
    assert gateway._D.marker_path != failover._D.marker_path


def test_failover_never_reserializes_a_response_body():
    """The one contract that keeps translation out of failover.py. If this ever
    fails, the translating code has grown into the module whose entire promise
    is that it forwards bytes untouched."""
    src = open(__import__('claude_sessions.failover', fromlist=['x']).__file__,
               encoding='utf-8').read()
    relay = src.split('def _relay(')[1].split('\n    def ')[0]
    assert 'json.dumps' not in relay
    assert 'json.loads' not in relay


# ── the URL chain ────────────────────────────────────────────

def test_the_gateway_takes_the_provider_slot_when_configured():
    """Downstream of `claude`, the gateway IS the Anthropic-speaking endpoint --
    the OpenAI-shaped host behind it cannot answer claude directly."""
    from claude_sessions import config as c
    s = dict(c._DEFAULT_SETTINGS, gateway_kind='openai', gateway_port=20130,
             provider_base_url='http://openai-host:8000/v1')
    assert c.provider_upstream(s) == 'http://127.0.0.1:20130'


def test_failover_still_wins_the_front_position():
    """chain: claude -> failover -> gateway -> host. Both proxies on must not
    mean the gateway is skipped."""
    from claude_sessions import config as c
    s = dict(c._DEFAULT_SETTINGS, gateway_kind='openai', provider_exec_model='m',
             provider_base_url='http://host', failover_models=['b'])
    assert c.provider_env(s)['ANTHROPIC_BASE_URL'] == 'http://127.0.0.1:20129'
    # ...and the failover proxy forwards to the gateway, not past it
    from claude_sessions import failover
    h = failover._Handler.__new__(failover._Handler)
    assert h._upstream(s) == 'http://127.0.0.1:20130'


def test_count_tokens_is_answered_rather_than_404ed():
    """Claude Code asks before falling back. Answering with the same chars//4
    floor claudectl uses elsewhere beats a 404 on every turn."""
    n = gateway._estimate_tokens({'system': [{'type': 'text', 'text': 'x' * 400}],
                                  'messages': [{'role': 'user', 'content': 'y' * 400}]})
    assert 150 < n < 250


def test_gateway_settings_are_declared_and_the_key_is_write_only():
    from claude_sessions import config as c
    for k in ('gateway_kind', 'gateway_port', 'gateway_target_base_url',
              'gateway_target_api_key'):
        assert k in c._DEFAULT_SETTINGS, k
    assert 'gateway_target_api_key' in c.INTERNAL_SETTINGS


def test_the_gateway_thread_actually_stops(monkeypatch):
    """The ping thread is a daemon, but a leaked one per stream would still pile
    up for the life of the process."""
    before = threading.active_count()
    tr = gateway.StreamTranslator('m')
    tr.feed({'choices': [{'delta': {'content': 'x'}}]})
    tr.finish()
    assert threading.active_count() == before


# ── end to end, over a real socket ───────────────────────────

def _fake_upstream(handler_body):
    """A tiny OpenAI-shaped server. Real sockets on both sides, because the
    translation bugs that matter (framing, ordering, close) do not reproduce
    against a stubbed urlopen."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            req = json.loads(self.rfile.read(n).decode() or '{}')
            code, ctype, body = handler_body(req)
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _post(port, path, payload, key=''):
    import http.client
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    c.request('POST', path, json.dumps(payload), headers)
    r = c.getresponse()
    return r.status, r.read()


def test_end_to_end_a_turn_survives_the_round_trip(monkeypatch):
    seen = {}

    def upstream(req):
        seen['req'] = req
        return 200, 'application/json', json.dumps({
            'id': 'x', 'model': 'qwen',
            'choices': [{'message': {'content': 'done'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 2}}).encode()

    up = _fake_upstream(upstream)
    from claude_sessions import config as c
    monkeypatch.setattr(c, 'load_settings', lambda: dict(
        c._DEFAULT_SETTINGS, provider_api_key='',
        gateway_target_base_url='http://127.0.0.1:%d/v1' % up.server_address[1]))
    gw = gateway.make_server(0)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    try:
        status, body = _post(gw.server_address[1], '/v1/messages', {
            'model': 'qwen', 'system': [{'type': 'text', 'text': 'sys'}],
            'messages': [{'role': 'user', 'content': 'hi'}]})
        assert status == 200
        out = json.loads(body)
        assert out['content'] == [{'type': 'text', 'text': 'done'}]
        assert seen['req']['messages'][0]['role'] == 'system'
    finally:
        gw.shutdown()
        up.shutdown()


def test_end_to_end_a_browser_request_is_refused(monkeypatch):
    """Same class of attack the failover proxy is guarded against, and the same
    reason: this daemon spends the user's upstream quota and sits on a fixed,
    source-published port."""
    from claude_sessions import config as c
    monkeypatch.setattr(c, 'load_settings', lambda: dict(
        c._DEFAULT_SETTINGS, gateway_target_base_url='http://127.0.0.1:1/v1'))
    gw = gateway.make_server(0)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    try:
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', gw.server_address[1], timeout=5)
        conn.request('POST', '/v1/messages', '{}',
                     {'Content-Type': 'application/json',
                      'Origin': 'https://evil.example'})
        assert conn.getresponse().status == 403
    finally:
        gw.shutdown()


def test_end_to_end_the_readiness_marker_answers_without_a_key(monkeypatch):
    """The probe cannot carry a credential, so it must be served before the
    guard -- otherwise ensure_running can never see its own daemon come up."""
    from claude_sessions import config as c
    monkeypatch.setattr(c, 'load_settings', lambda: dict(
        c._DEFAULT_SETTINGS, provider_api_key='required',
        gateway_target_base_url='http://127.0.0.1:1/v1'))
    gw = gateway.make_server(0)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    try:
        assert gateway._D.is_ready(gw.server_address[1])
    finally:
        gw.shutdown()
