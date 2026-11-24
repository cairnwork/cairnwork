"""Relay matches the experiment: exec bit kept, 9B thinking off, 200 steps / 960 s."""

from __future__ import annotations

import http.client
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from experimental.local_relay.config import RelayConfig
from experimental.local_relay.thinking_shim import DEFAULT_EXTRA_BODY, merge_extra, thinking_off
from experimental.local_relay.worker_launch import worker_argv
from experimental.local_relay.worker_setup import prepare_worker_layout


class _Upstream(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, bytes]] = []

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _reply(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        data = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append(("POST", self.path, data))
        self._reply(b'{"ok": true}')

    def do_GET(self):  # noqa: N802
        type(self).seen.append(("GET", self.path, b""))
        self._reply(b'{"data": []}')


def _post(url: str, path: str, payload: dict) -> dict:
    host, port = url.split("//")[1].split("/")[0].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request(
        "POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"}
    )
    return json.loads(conn.getresponse().read())


def test_shim_adds_thinking_off_to_chat_only_and_listens_on_loopback():
    _Upstream.seen = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{upstream.server_address[1]}/v1"
        with thinking_off(base) as url:
            assert url.startswith("http://127.0.0.1:") and url.endswith("/v1")
            assert _post(url, "/v1/chat/completions", {"model": "m", "messages": []}) == {
                "ok": True
            }
            _post(url, "/tokenize", {"content": "x"})
    finally:
        upstream.shutdown()
        upstream.server_close()
    chat = json.loads(_Upstream.seen[0][2])
    assert chat["chat_template_kwargs"] == {"enable_thinking": False}
    assert chat["reasoning_effort"] == "none" and chat["model"] == "m"
    assert json.loads(_Upstream.seen[1][2]) == {"content": "x"}  # other paths untouched


def test_merge_extra_leaves_non_object_payloads_alone():
    assert merge_extra(b"[1]", DEFAULT_EXTRA_BODY) == b"[1]"
    assert merge_extra(b"not json", DEFAULT_EXTRA_BODY) == b"not json"


def test_worker_argv_uses_experiment_limits_by_default_and_config_can_override():
    parts = (Path("t"), Path("c"), Path("o"), Path("r"), Path("runs"))
    task = {"allowed_files": ["a.py"]}
    argv = worker_argv(task, parts, "m", 32768, "http://127.0.0.1:1/v1")
    assert argv[argv.index("--max-steps") + 1] == "200"
    assert argv[argv.index("--max-wall-seconds") + 1] == "960.0"
    config = RelayConfig()
    assert (config.max_steps, config.max_wall_seconds) == (200, 960.0)
    argv = worker_argv(task, parts, "m", 1, "http://127.0.0.1:1/v1", 7, 5.0)
    assert argv[argv.index("--max-steps") + 1] == "7"


def test_checkout_layout_keeps_executable_bit(monkeypatch, tmp_path):
    from experimental.local_relay import worker_setup

    monkeypatch.setattr(worker_setup, "_worker_python", lambda: "/tool/python3.13")
    monkeypatch.setattr(worker_setup.subprocess, "run", lambda *_a, **_k: None)
    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o750)
    checkout = run_root / "checkout"
    (checkout / "bin").mkdir(parents=True)
    plain, tool = checkout / "a.py", checkout / "bin" / "tool.sh"
    plain.write_text("x\n")
    tool.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    tool.chmod(0o755)
    prepare_worker_layout(run_root, checkout)
    assert stat.S_IMODE(plain.stat().st_mode) == 0o660
    assert stat.S_IMODE(tool.stat().st_mode) == 0o770
    assert os.access(tool, os.X_OK)
