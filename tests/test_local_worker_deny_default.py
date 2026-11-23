"""#326: the local-worker Seatbelt profile starts from ``(deny default)`` (no ``(allow default)``
left in the tool), and a Mach service the profile does not name cannot be looked up."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from local_worker_fakes import TAG, WRAPPER, make_runs

LOOKUP = (
    "import ctypes, ctypes.util\n"
    "lib = ctypes.CDLL(ctypes.util.find_library('System'))\n"
    "port = ctypes.c_uint32(0)\n"
    "lib.bootstrap_look_up.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p]\n"
    "bp = ctypes.c_uint32.in_dll(lib, 'bootstrap_port').value\n"
    "print('LOOKUP', lib.bootstrap_look_up(bp, b'com.apple.cfprefsd.daemon', ctypes.byref(port)))\n"
)
NETWORK = (
    "import socket\n"
    "s = socket.socket()\n"
    "s.settimeout(5)\n"
    "print('NET', s.connect_ex(('127.0.0.1', PORT)))\n"
)


def test_the_profile_head_is_deny_default_and_nothing_allows_default():
    text = WRAPPER.read_text()
    assert "(allow default)" not in text
    head = text.split("profile_head() {", 1)[1].split("\n}", 1)[0]
    assert "(version 1)\\n(deny default)" in head


def _probe(code: str, tag: str, *, sandboxed: bool, tmp_path) -> int:
    base = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    if not sandboxed:  # the control: the same code, no sandbox, must find the service
        out = subprocess.run([base, "-c", code], capture_output=True, text=True, check=True)
        return int(out.stdout.split(tag)[1])
    from experimental.local_worker.tools import WorkerTools, subprocess_invoker

    runs = make_runs(tmp_path)
    py = runs / TAG / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f"#!/bin/sh\nexec {base} -c \"$(cat <<'EOF'\n{code}EOF\n)\"\n")
    py.chmod(0o755)
    env = dict(
        os.environ,
        CAIRNWORK_LOCAL_WORKER_RUNS=str(runs),
        CAIRNWORK_LOCAL_WORKER_PYROOT=sys.base_prefix,
    )
    tools = WorkerTools(
        runs / TAG,
        tmp_path / "calls.jsonl",
        mode="direct",
        wrapper=WRAPPER,
        invoker=subprocess_invoker(env),
        timeout_seconds=60,
    )
    call = tools.run_tests(["-q"])
    assert tag in call.output, call.output
    return int(call.output.split(tag)[1].split()[0])


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt")
def test_a_mach_service_the_profile_does_not_name_is_denied(tmp_path):
    assert _probe(LOOKUP, "LOOKUP", sandboxed=False, tmp_path=tmp_path) == 0
    assert _probe(LOOKUP, "LOOKUP", sandboxed=True, tmp_path=tmp_path) != 0


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt")
def test_a_network_connect_is_refused_inside_the_profile(tmp_path):
    import socket

    with socket.socket() as listener:  # a live listener: only policy can refuse the connect
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        code = NETWORK.replace("PORT", str(listener.getsockname()[1]))
        assert _probe(code, "NET", sandboxed=False, tmp_path=tmp_path) == 0
        assert _probe(code, "NET", sandboxed=True, tmp_path=tmp_path) != 0
