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


def test_the_profile_head_is_deny_default_and_nothing_allows_default():
    text = WRAPPER.read_text()
    assert "(allow default)" not in text
    head = text.split("profile_head() {", 1)[1].split("\n}", 1)[0]
    assert "(version 1)\\n(deny default)" in head


def _lookup_code(*, sandboxed: bool, tmp_path) -> int:
    base = os.path.realpath(getattr(sys, "_base_executable", sys.executable))
    if not sandboxed:  # the control: the same code, no sandbox, must find the service
        out = subprocess.run([base, "-c", LOOKUP], capture_output=True, text=True, check=True)
        return int(out.stdout.split("LOOKUP")[1])
    from experimental.local_worker.tools import WorkerTools, subprocess_invoker

    runs = make_runs(tmp_path)
    py = runs / TAG / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f"#!/bin/sh\nexec {base} -c \"$(cat <<'EOF'\n{LOOKUP}EOF\n)\"\n")
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
    assert "LOOKUP" in call.output, call.output
    return int(call.output.split("LOOKUP")[1].split()[0])


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt")
def test_a_mach_service_the_profile_does_not_name_is_denied(tmp_path):
    assert _lookup_code(sandboxed=False, tmp_path=tmp_path) == 0
    assert _lookup_code(sandboxed=True, tmp_path=tmp_path) != 0
