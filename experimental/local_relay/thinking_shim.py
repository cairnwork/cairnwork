"""Loopback shim that switches thinking off for the local 9B, as the experiment did.

The local-worker CLI sends a fixed chat payload. This shim forwards every request to the
real loopback llama-server and, for ``/chat/completions``, merges ``extra_body`` into the JSON
payload (default: ``chat_template_kwargs.enable_thinking=false``, ``reasoning_effort=none``).
It listens on 127.0.0.1 only, on an ephemeral port, for the lifetime of one worker run.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from experimental.local_worker.endpoint import parse_loopback_url

DEFAULT_EXTRA_BODY: dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "reasoning_effort": "none",
}
MAX_BODY = 16 * 1024 * 1024
UPSTREAM_TIMEOUT = 1800.0


def merge_extra(raw: bytes, extra: dict[str, Any]) -> bytes:
    """Merge ``extra`` into a JSON object payload; anything else is forwarded unchanged."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(payload, dict):
        return raw
    return json.dumps({**payload, **extra}).encode()


def _handler(upstream: str, extra: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    target = urlsplit(upstream)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _forward(self, body: bytes | None) -> None:
            conn = http.client.HTTPConnection(
                target.hostname, target.port, timeout=UPSTREAM_TIMEOUT
            )
            try:
                headers = {"Content-Type": "application/json"} if body is not None else {}
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read(MAX_BODY + 1)
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (OSError, http.client.HTTPException):
                self.send_error(502)
            finally:
                conn.close()

        def do_GET(self) -> None:  # noqa: N802
            self._forward(None)

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            if self.path.endswith("/chat/completions"):
                body = merge_extra(body, extra)
            self._forward(body)

    return Handler


@contextmanager
def thinking_off(endpoint: str, extra: dict[str, Any] | None = None) -> Iterator[str]:
    """Yield the shim's loopback URL (same path as ``endpoint``) while the shim runs."""
    upstream = str(parse_loopback_url(endpoint))
    base = urlsplit(upstream)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(upstream, extra or DEFAULT_EXTRA_BODY))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}{base.path}"
    finally:
        server.shutdown()
        server.server_close()
