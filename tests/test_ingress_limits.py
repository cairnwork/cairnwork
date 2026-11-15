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
