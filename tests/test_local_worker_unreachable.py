"""Connection-level endpoint failures get their own durable abort cause."""

from __future__ import annotations

import errno
import http.client
import http.server
import socket
import threading
import time

import pytest

from experimental.local_worker import schema_rules
from experimental.local_worker.endpoint import (
    EndpointError,
    EndpointUnreachable,
    LoopbackEndpoint,
)


def test_refused_connection_is_endpoint_unreachable() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = LoopbackEndpoint(f"http://127.0.0.1:{port}/v1")
    with pytest.raises(EndpointUnreachable):
        endpoint.chat({"messages": []}, time.monotonic() + 5)


def test_pinned_abort_cause_vocabulary_keeps_old_values() -> None:
    assert schema_rules.ABORT_CAUSES[:6] == (
        "endpoint_error",
        "worker_isolation",
        "status_unavailable",
        "protocol_error",
        "internal_error",
        "interrupted",
    )
    assert schema_rules.ABORT_CAUSES[-1] == "endpoint_unreachable"
    assert len(schema_rules.ABORT_CAUSES) == 7


class _ErrorHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *_args: object) -> None:  # noqa: A002 - matches BaseHTTPRequestHandler
        pass

    def do_POST(self) -> None:
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_oom_or_http_error_stays_endpoint_error() -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), _ErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = LoopbackEndpoint(f"http://127.0.0.1:{server.server_address[1]}/v1")
        with pytest.raises(EndpointError) as caught:
            endpoint.chat({"messages": []}, time.monotonic() + 5)
        assert type(caught.value) is EndpointError
        assert not isinstance(caught.value, EndpointUnreachable)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("code", [errno.ENETUNREACH, errno.EHOSTUNREACH])
def test_no_route_errors_are_endpoint_unreachable(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def refuse(self: http.client.HTTPConnection) -> None:
        raise OSError(code, "no route")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)
    endpoint = LoopbackEndpoint("http://127.0.0.1:9/v1")
    with pytest.raises(EndpointUnreachable):
        endpoint.chat({"messages": []}, time.monotonic() + 5)


def test_other_os_errors_stay_endpoint_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: http.client.HTTPConnection) -> None:
        raise OSError(errno.EPIPE, "broken pipe")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", broken)
    endpoint = LoopbackEndpoint("http://127.0.0.1:9/v1")
    with pytest.raises(EndpointError) as caught:
        endpoint.chat({"messages": []}, time.monotonic() + 5)
    assert type(caught.value) is EndpointError


def test_refusal_after_connecting_is_not_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connection is up (a real listening socket), so the request phase runs; a refusal
    raised there is an endpoint_error, not unreachable (Codex review of PR #1421)."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _ErrorHandler)  # listens; accepts later
    try:

        def sent(self: http.client.HTTPConnection, *_args: object, **_kwargs: object) -> None:
            assert self.sock is not None  # the request phase is reached only after connecting
            raise OSError(errno.ECONNREFUSED, "reset after connect")

        monkeypatch.setattr(http.client.HTTPConnection, "request", sent)
        endpoint = LoopbackEndpoint(f"http://127.0.0.1:{server.server_address[1]}/v1")
        with pytest.raises(EndpointError) as caught:
            endpoint.chat({"messages": []}, time.monotonic() + 5)
        assert type(caught.value) is EndpointError
    finally:
        server.server_close()
