"""The context-window guard of the local worker loop (#88 robustness): a request whose prompt and
output budget exceed the window is never sent; the episode ends with CONTEXT_LIMIT."""

from __future__ import annotations

import copy
import http.server
import json
import threading
import time
from typing import Any

import pytest
from local_worker_fakes import META, TASK, make_loop, reply

from experimental.local_worker import endpoint as ep
from experimental.local_worker import episode
from experimental.local_worker.agent import initial_messages
from experimental.local_worker.episode_schema import validate_record
from experimental.local_worker.guards import prompt_bound

READ = reply("read_file", path="src/app.py")


def first_bound(tmp_path) -> int:
    loop, *_ = make_loop(tmp_path / "probe", [])
    return prompt_bound(loop._payload(initial_messages(TASK)), None)


def counting(endpoint, value: Any) -> list[dict]:
    """Give the fake endpoint a count_tokens answering ``value`` (an int, or raised)."""
    seen: list[dict] = []

    def count(payload: dict, deadline: float) -> int:
        seen.append(copy.deepcopy(payload))
        if isinstance(value, BaseException):
            raise value
        return value

    endpoint.count_tokens = count
    return seen


def run(loop, messages=None, **kw):
    out = loop.run(messages or initial_messages(TASK), **kw)
    rec = episode.build_record(META, out)
    validate_record(rec)
    return out, rec


def test_a_prompt_well_inside_the_window_is_never_counted(tmp_path):
    loop, _, endpoint, _ = make_loop(tmp_path, [READ, READ])
    seen = counting(endpoint, 10**9)
    out, _ = run(loop)
    assert out.exit_reason == "COMPLETED" and seen == []


def test_over_the_bound_the_exact_count_decides(tmp_path):
    window = first_bound(tmp_path)  # room = window - 100: the bound does not fit
    loop, _, endpoint, _ = make_loop(
        tmp_path, [READ], context_window=window, max_completion_tokens=100
    )
    seen = counting(endpoint, window - 100)  # exactly the room: it fits
    out, _ = run(loop)
    assert out.exit_reason == "COMPLETED" and len(seen) == 1 and len(endpoint.payloads) == 2
    assert seen[0] == endpoint.payloads[0]  # it counted the very request it then sent


def test_an_exact_count_over_the_room_ends_before_sending(tmp_path):
    window = first_bound(tmp_path)
    loop, world, endpoint, _ = make_loop(
        tmp_path, [READ], context_window=window, max_completion_tokens=100
    )
    counting(endpoint, window - 99)
    out, rec = run(loop)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window")
    assert endpoint.payloads == [] and rec["total_turns"] == 0
    assert rec["termination"]["cleanup"] == "ok" and out.abort_cause is None


@pytest.mark.parametrize("count", [None, ep.EndpointError("HTTP 404")])
def test_an_endpoint_that_cannot_count_ends_on_the_bound(tmp_path, count):
    window = first_bound(tmp_path)
    loop, _, endpoint, _ = make_loop(
        tmp_path, [READ], context_window=window, max_completion_tokens=100
    )
    if count is not None:
        counting(endpoint, count)
    out, _ = run(loop)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window_bound")
    assert endpoint.payloads == []


def test_a_count_past_the_deadline_is_a_timeout(tmp_path):
    window = first_bound(tmp_path)
    loop, _, endpoint, _ = make_loop(
        tmp_path, [READ], context_window=window, max_completion_tokens=100
    )
    counting(endpoint, ep.EndpointTimeout("deadline passed"))
    out, _ = run(loop)
    assert out.exit_reason == "TIMEOUT" and endpoint.payloads == []


def test_the_last_reported_prompt_plus_what_was_added_is_checked_each_turn(tmp_path):
    room = 60_000 - 4096
    big = reply("read_file", usage=(room - 50, 10, None), path="src/app.py")  # 50 tokens left
    loop, world, endpoint, _ = make_loop(tmp_path, [big, READ], context_window=60_000)
    seen = counting(endpoint, room + 1)
    out, rec = run(loop)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window")
    assert len(endpoint.payloads) == 1 and len(seen) == 1 and rec["total_turns"] == 1
    assert [c[0] for c in world.calls].count("read") == 1  # the turn ran, the next was not sent


def test_a_400_context_too_long_is_context_limit_not_an_endpoint_error(tmp_path):
    too_long = ep.EndpointContextLimit("endpoint answered HTTP 400: context too long")
    loop, _, endpoint, _ = make_loop(tmp_path, [READ, too_long])
    out, rec = run(loop)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window_http_400")
    assert out.abort_cause is None and rec["total_turns"] == 1


def test_a_resume_prefix_over_the_window_is_never_sent(tmp_path):
    loop, _, endpoint, _ = make_loop(tmp_path, [READ])
    messages = initial_messages(TASK)
    messages.append({"role": "user", "content": "x" * 70_000})  # bytes bound the tokens
    out, _ = run(loop, messages, first_turn=3, elapsed_offset=10.0)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window_bound")
    assert endpoint.payloads == []


def test_the_bound_never_undercounts_a_byte_level_tokenizer():
    payload = {"tools": [{"x": 1}], "messages": [{"role": "user", "content": "é" * 100}]}
    assert prompt_bound(payload, None) >= len(json.dumps(payload).encode())
    assert prompt_bound(payload, (1, 7)) == 7  # nothing added since the counted request
    later = {"messages": [*payload["messages"], {"role": "tool", "content": "ab"}]}
    assert prompt_bound(later, (1, 7)) == 7 + len(json.dumps(later["messages"][1])) + 32


# -- the HTTP side: a 400 body and Splash's count ------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, bytes]] = {}
    seen: list[tuple[str, dict]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((self.path, body))
        status, data = self.routes.get(self.path, (404, b'{"error":"not found"}'))
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    _Handler.routes, _Handler.seen = {}, []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield _Handler, f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


SPLASH_400 = (
    b'{"error":{"message":"prompt and max_completion_tokens exceed the context window",'
    b'"type":"invalid_request_error","code":"context_length_exceeded"}}'
)


@pytest.mark.parametrize(
    "body",
    [
        SPLASH_400,
        b'{"error":{"message":"This model\'s maximum context length is 65536 tokens"}}',
        b'{"error":{"type":"exceed_context_size_error"}}',
        b'{"error":"the request exceeds the available context size"}',
    ],
)
def test_a_context_400_maps_to_the_context_limit(server, body):
    handler, url = server
    handler.routes = {"/v1/chat/completions": (400, body)}
    with pytest.raises(ep.EndpointContextLimit):
        ep.LoopbackEndpoint(url).chat({"messages": []}, time.monotonic() + 5)


@pytest.mark.parametrize("status_body", [(400, b'{"error":"bad tool schema"}'), (500, SPLASH_400)])
def test_other_errors_stay_endpoint_errors(server, status_body):
    handler, url = server
    handler.routes = {"/v1/chat/completions": status_body}
    with pytest.raises(ep.EndpointError) as caught:
        ep.LoopbackEndpoint(url).chat({"messages": []}, time.monotonic() + 5)
    assert not isinstance(caught.value, ep.EndpointContextLimit)


def test_count_tokens_asks_splash_for_the_template_then_the_tokens(server):
    handler, url = server
    handler.routes = {
        "/apply-template": (200, b'{"prompt":"<|im_start|>user hi"}'),
        "/tokenize": (200, b'{"tokens":[1,2,3,4,5]}'),
    }
    payload = {"messages": [{"role": "user", "content": "hi"}], "tools": []}
    assert ep.LoopbackEndpoint(url).count_tokens(payload, time.monotonic() + 5) == 5
    (p1, b1), (p2, b2) = handler.seen
    assert p1 == "/apply-template" and b1 == {**payload, "add_generation_prompt": True}
    assert p2 == "/tokenize" and b2 == {"content": "<|im_start|>user hi", "add_special": False}


@pytest.mark.parametrize(
    "routes",
    [
        {},
        {"/apply-template": (200, b'{"text":"x"}')},
        {"/apply-template": (200, b'{"prompt":"x"}'), "/tokenize": (200, b'{"n":3}')},
    ],
)
def test_a_server_that_cannot_count_raises_an_endpoint_error(server, routes):
    handler, url = server
    handler.routes = routes
    with pytest.raises(ep.EndpointError):
        ep.LoopbackEndpoint(url).count_tokens({"messages": []}, time.monotonic() + 5)


# -- the first request's fixed part (review of PR 251, 2) ------------------------------------------
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("slack", [-1, 0])
def test_the_first_bound_counts_the_tools_and_the_template(tmp_path, resume, slack):
    """With no reported count (a fresh start or a resume), the bound is the messages plus the tool
    schemas' bytes plus FIRST_OVERHEAD: a window the messages alone fit but that sum does not is
    never sent to (slack -1), and the exact sum fits (slack 0)."""
    from experimental.local_worker.guards import MESSAGE_OVERHEAD, json_bytes

    probe, *_ = make_loop(tmp_path / "probe", [])
    messages = initial_messages(TASK)
    payload = probe._payload(messages)
    alone = sum(json_bytes(m) + MESSAGE_OVERHEAD for m in messages)
    room = alone + json_bytes(payload["tools"]) + 1024 + slack  # FIRST_OVERHEAD, as documented
    loop, _, endpoint, _ = make_loop(
        tmp_path, [READ], context_window=room + 100, max_completion_tokens=100
    )
    kw = {"first_turn": 3, "elapsed_offset": 10.0} if resume else {}
    out = loop.run(initial_messages(TASK), **kw)  # a resume's record needs its parent fields
    if slack < 0:
        assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window_bound")
        assert endpoint.payloads == []
    else:
        assert len(endpoint.payloads) >= 1


def test_without_usage_only_the_context_guard_decides(tmp_path):
    """Review of PR 251 (6): the old 2-bytes-per-token estimate (context_cap_bytes_estimate) is
    gone; with no reported usage the context-window guard's byte bound alone decides."""
    replies = [reply("read_file", usage="missing", path=p) for p in ("src/app.py", "x")] * 2
    loop, _, _, _ = make_loop(tmp_path, replies, context_cap=200)
    out, rec = run(loop)
    assert out.exit_reason == "COMPLETED" and rec["total_turns"] == 5
    loop, _, endpoint, _ = make_loop(tmp_path / "b", replies, context_window=4200)  # room: 104
    out, _ = run(loop)
    assert (out.exit_reason, out.limit_hit) == ("CONTEXT_LIMIT", "context_window_bound")
    assert endpoint.payloads == []
