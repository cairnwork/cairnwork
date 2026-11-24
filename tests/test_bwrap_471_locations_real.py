"""#471 step 2a on a real bwrap: which host pathname-socket locations a Linux run can connect to.

One test per location class and identity (owner = same uid; verifier account = another uid). The
test makes a listening socket in the class outside the sandbox, then the sandboxed process tries
one connect to it. /tmp is the control: it is a private tree, so it must stay unreachable (the
#391 guarantee, no regression). The other classes are pinned as they are today, and the EXPECTED
table is the one place to edit when #471 step 2b closes a class. A class listed as open is a
tracking pin, not a promise. Linux CI only: without bwrap the tests skip; on Linux with
CAIRNWORK_REQUIRE_SANDBOX=1 a missing bwrap fails. Classes that need root to create the directory
(/srv, /opt, /var/lib) skip when passwordless sudo is not available.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from cairnwork_sandbox import bwrap

pytestmark = pytest.mark.sandbox

# (class, owner can connect, verifier account can connect); True = open today (#471).
EXPECTED = {
    "tmp": (False, False),  # control: private tree since #391
    "var-tmp": (False, False),  # private tree since #471 step 2b
    "home": (True, False),  # the account cannot enter the owner's 0700 home
    "srv": (True, True),
    "opt": (True, True),
    "var-lib": (True, True),
}
ROOT_DIRS = {"srv": "/srv", "opt": "/opt", "var-lib": "/var/lib"}
CONNECT = (
    "import socket,sys\n"
    "s=socket.socket(socket.AF_UNIX)\n"
    "s.settimeout(5)\n"
    "try:\n"
    "    s.connect(sys.argv[1])\n"
    "    print('connectable')\n"
    "except OSError as e:\n"
    "    print('not-connectable')\n"
)


@pytest.fixture(autouse=True)
def _real_bwrap():
    if bwrap.available():
        return
    if os.environ.get("CAIRNWORK_REQUIRE_SANDBOX") == "1" and sys.platform == "linux":
        pytest.fail("CAIRNWORK_REQUIRE_SANDBOX=1 but bwrap is unavailable")
    pytest.skip("needs a working bwrap (Linux)")


def _sudo(*args: str) -> bool:
    argv = ["sudo", "-n", *args]
    return subprocess.run(argv, capture_output=True, timeout=30, check=False).returncode == 0  # noqa: S603


@pytest.fixture
def owner_home() -> Iterator[Path]:
    """A 0700 base under the user's home, as the other real-bwrap tests build."""
    base = Path(tempfile.mkdtemp(prefix="cw471h-", dir=Path.home())).resolve()
    base.chmod(0o700)
    (base / "home").mkdir()
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _make_dir(kind: str, owner_home: Path) -> tuple[Path, bool]:
    """Directory in the class, readable by everyone; the bool says to remove it with sudo."""
    if kind in ROOT_DIRS:
        path = Path(ROOT_DIRS[kind]) / f"cw471-{os.getpid()}"
        if not _sudo("install", "-d", "-m", "0777", str(path)):
            pytest.skip(f"needs passwordless sudo to create a directory in the {kind} class")
        return path, True
    if kind == "home":
        path = owner_home / "home" / "sock"
        path.mkdir()
        return path, False
    parent = {"tmp": "/tmp", "var-tmp": "/var/tmp"}[kind]  # noqa: S108
    path = Path(tempfile.mkdtemp(prefix="cw471-", dir=parent))
    path.chmod(0o755)
    return path, False


@pytest.fixture
def listener(request, owner_home) -> Iterator[tuple[str, str]]:
    """(class, socket path): a listening socket 0666 in a directory of the class."""
    kind = request.param
    directory, via_sudo = _make_dir(kind, owner_home)
    path = directory / "probe.sock"
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(str(path))
        path.chmod(0o666)
        server.listen()
        yield kind, str(path)
    finally:
        server.close()
        if via_sudo:
            _sudo("rm", "-rf", str(directory))
        else:
            shutil.rmtree(directory, ignore_errors=True)


def _policy(owner_home: Path, owner: bool) -> Path:
    policy = bwrap.BACKEND.policy((), (), home=owner_home / "home", mask_home_credentials=owner)
    path = owner_home / "policy.txt"
    path.write_text(policy.text)
    return path


def _connectable(output: bytes) -> bool:
    text = output.decode(errors="replace").strip()
    assert text in ("connectable", "not-connectable"), text
    return text == "connectable"


def _owner_connect(owner_home: Path, sock: str) -> bool:
    argv = bwrap.wrap([sys.executable, "-I", "-c", CONNECT, sock], _policy(owner_home, True))
    done = subprocess.run(argv, capture_output=True, timeout=60, check=False)  # noqa: S603
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    return _connectable(done.stdout)


def _verifier_connect(owner_home: Path, sock: str) -> bool:
    from cairnwork.verifier_account import VerifierAccount

    account = VerifierAccount()
    with account.exclusive(60):
        done = subprocess.run(  # noqa: S603
            account.wrap(
                ["/usr/bin/python3", "-I", "-c", CONNECT, sock], _policy(owner_home, False)
            ),
            capture_output=True,
            timeout=70,
            check=False,
            cwd=account.root,
        )
    assert done.returncode == 0, done.stderr.decode(errors="replace")
    return _connectable(done.stdout)


@pytest.mark.parametrize("listener", list(EXPECTED), indirect=True)
def test_owner_location_reachability(listener, owner_home):
    kind, sock = listener
    assert _owner_connect(owner_home, sock) is EXPECTED[kind][0], kind


@pytest.mark.verifier_account
@pytest.mark.parametrize("listener", list(EXPECTED), indirect=True)
def test_verifier_location_reachability(listener, owner_home):
    kind, sock = listener
    assert _verifier_connect(owner_home, sock) is EXPECTED[kind][1], kind


def test_the_probe_itself_connects_outside_the_sandbox(tmp_path):
    """Without this, 'not connectable' in the tests above could just mean the probe is broken."""
    sock = str(Path(tempfile.mkdtemp(prefix="cw471p-", dir="/var/tmp")) / "p.sock")  # noqa: S108
    server = socket.socket(socket.AF_UNIX)
    try:
        server.bind(sock)
        server.listen()
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-I", "-c", CONNECT, sock], capture_output=True, timeout=30, check=True
        )
        assert _connectable(done.stdout)
    finally:
        server.close()
        shutil.rmtree(Path(sock).parent, ignore_errors=True)
