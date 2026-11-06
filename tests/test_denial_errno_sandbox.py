"""#854 (#797 step 3): which errno each kind of sandbox denial gives a run, on each backend.

report_guard counts a wrapped OSError as environment only for EACCES, EPERM, EROFS and
ENETUNREACH, or a failed host-name lookup of any errno (#932, test_wrapped_dns_env.py). This
pins what the real backends return, so a backend change that moves a denial
to another errno (and so out of reach of that rule) fails here. A ``None`` row is an operation
the backend does not deny at all: on bwrap the host root is mounted read-only, so a read outside
the read roots succeeds. bwrap's network namespace has its own loopback, so a connect there is
refused, not denied. Measured rows only; the known gaps are in docs/PRESERVED_BEHAVIORS.md."""

import json
import subprocess
import sys

import pytest

import cairnwork_sandbox
from cairnwork.pytest_config import interpreter_roots

pytestmark = pytest.mark.sandbox

PROBE = """
import errno, json, os, socket, sys
outside, host_file = sys.argv[1], sys.argv[2]
def code(step):
    try:
        step()
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
    return None
def connect(host):
    with socket.socket() as s:
        s.settimeout(3)
        s.connect((host, 9))
print(json.dumps({
    "read_outside_roots": code(lambda: open(os.path.join(outside, "f")).close()),
    "read_host_file": code(lambda: open(host_file).close()),
    "write_outside_roots": code(lambda: open(os.path.join(outside, "new"), "w").close()),
    "write_host_dir": code(lambda: open("/usr/cairnwork-errno-probe", "w").close()),
    "connect_loopback": code(lambda: connect("127.0.0.1")),
    "connect_external": code(lambda: connect("192.0.2.1")),  # TEST-NET-1: never a real host
}))
"""

MEASURED = {
    "seatbelt": {
        "read_outside_roots": "EPERM",
        "read_host_file": "EPERM",
        "write_outside_roots": "EPERM",
        "write_host_dir": "EPERM",
        "connect_loopback": "EPERM",
        "connect_external": "EPERM",
    },
    "bwrap": {
        # measured on CI test-linux-sandbox (x86-64 and arm64), #882
        "read_outside_roots": "ENOENT",  # /tmp is a private tree: the path is not there at all
        "read_host_file": None,  # the root is bound read-only: reading is not denied
        "write_outside_roots": "ENOENT",  # the same private /tmp
        "write_host_dir": "EROFS",
        "connect_loopback": "ECONNREFUSED",  # the namespace has its own loopback: nothing listens
        "connect_external": "ENETUNREACH",  # a new network namespace: no route out
    },
}


def test_each_denial_gives_the_errno_this_table_names(tmp_path):
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name not in MEASURED:
        pytest.skip("no sandbox backend on this host")
    root, outside = tmp_path / "rw", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "f").write_text("x")
    host_file = "/etc/protocols" if sys.platform == "darwin" else "/etc/hostname"
    policy = backend.policy(interpreter_roots(sys.executable), (root,))
    path = root / "policy"
    path.write_text(policy.text)
    argv = backend.wrap([sys.executable, "-I", "-c", PROBE, str(outside), host_file], path)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=root)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == MEASURED[backend.name]
