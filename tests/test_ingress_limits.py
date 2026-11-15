from __future__ import annotations

import json
import socket
import threading
from http.client import HTTPConnection

import pytest

from cairnwork_protocol import ingress
from cairnwork_protocol.ingress import IngressError, check_bind, serve


@pytest.mark.parametrize("timeout", [1e10, 10**400])
def test_serve_rejects_timeout_above_cap(timeout):
    with pytest.raises(IngressError) as err:
        serve("127.0.0.1", 0, "token", {}, timeout=timeout)
    assert err.value.code == "INGRESS_TIMEOUT_INVALID"


def test_localhost_rejects_any_non_loopback_resolution(monkeypatch):
    monkeypatch.setattr(
        ingress.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 0)),
        ],
    )
    with pytest.raises(IngressError) as err:
        check_bind("localhost", 8080)
    assert err.value.code == "INGRESS_BIND_NOT_LOOPBACK"


@pytest.fixture
def ingress_server():
    httpd = serve("127.0.0.1", 0, "token", {"/ok": lambda body: (200, {})}, timeout=2)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_port
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.mark.parametrize(
    ("status", "expected", "path", "headers", "length"),
    [
        (401, "INGRESS_UNAUTHORIZED", "/ok", {}, 6),
        (404, "INGRESS_NOT_FOUND", "/missing", {"Authorization": "Bearer token"}, 6),
        (
            413,
            "INGRESS_BODY_TOO_LARGE",
            "/ok",
            {"Authorization": "Bearer token"},
            (1 << 20) + 1,
        ),
        (
            421,
            "INGRESS_HOST_NOT_ALLOWED",
            "/ok",
            {"Authorization": "Bearer token", "Host": "example.com"},
            6,
        ),
    ],
)
def test_early_refusals_with_body_return_fixed_json(
    ingress_server, status, expected, path, headers, length
):
    conn = HTTPConnection("127.0.0.1", ingress_server, timeout=5)
    conn.putrequest("POST", path, skip_host="Host" in headers)
    for name, value in headers.items():
        conn.putheader(name, value)
    conn.putheader("Content-Length", str(length))
    conn.endheaders()
    body = b"x" * length
    conn.send(body)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    assert response.status == status
    assert json.loads(data) == {"error": expected}


def test_early_refusal_with_stalled_body_answers_promptly():
    import time

    httpd = serve("127.0.0.1", 0, "token", {"/ok": lambda body: (200, {})}, timeout=30)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
        conn.putrequest("POST", "/ok")
        conn.putheader("Content-Length", "100000")
        conn.endheaders()
        conn.send(b"x" * 10)  # the rest never arrives
        start = time.monotonic()
        response = conn.getresponse()
        data = response.read()
        conn.close()
        assert response.status == 401
        assert json.loads(data) == {"error": "INGRESS_UNAUTHORIZED"}
        assert time.monotonic() - start < 2.5
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def _raise_422(body):
    raise IngressError("X", 422)


def test_route_refusal_after_full_body_answers_promptly():
    import time

    httpd = serve("127.0.0.1", 0, "token", {"/r": _raise_422}, timeout=3)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
        conn.request("POST", "/r", body=b"abcdef", headers={"Authorization": "Bearer token"})
        start = time.monotonic()
        response = conn.getresponse()
        data = response.read()
        conn.close()
        assert response.status == 422
        assert json.loads(data) == {"error": "X"}
        assert time.monotonic() - start < 1.5
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize("timeout", [3600.0001, 3601, -(10**400), float("nan"), float("inf"), True])
def test_more_invalid_timeouts(timeout):
    with pytest.raises(IngressError):
        serve("127.0.0.1", 0, "token", {}, timeout=timeout)


@pytest.mark.parametrize("timeout", [3600, 3600.0])
def test_cap_itself_is_accepted(timeout):
    serve("127.0.0.1", 0, "token", {}, timeout=timeout).server_close()


@pytest.mark.parametrize("result", [[], socket.gaierror("x"), OSError("x")])
def test_localhost_resolution_failure_is_refused(monkeypatch, result):
    def fake(*_a, **_k):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ingress.socket, "getaddrinfo", fake)
    with pytest.raises(IngressError) as err:
        check_bind("localhost", 8080)
    assert err.value.code == "INGRESS_BIND_NOT_LOOPBACK"
