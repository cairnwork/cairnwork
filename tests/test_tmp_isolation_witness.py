"""#391: a host socket under /tmp is not connectable from a run, in owner and account mode.

Host-independent, unlike the walk in test_run_isolation_witness.py: the outer test plants its own
sockets under /tmp (a listening STREAM and a bound DGRAM socket, mode 0777 in a 0755 directory,
so the verifier account could connect without the sandbox) before the run. Right after the policy
is built it creates another, and a second about a second later, while the run is starting or in
progress; the policy text must not name them. The candidate polls all of them for a few seconds.
Each run has its own read-only tmpfs at /tmp in its own mount namespace, so a socket the host adds
later does not appear inside. On Linux the candidate first connects to a socket of its own in
TMPDIR, so a probe that cannot connect at all does not pass.
"""

import inspect
import os
import shutil
import socket
import threading

import pytest
from run_root_scope import own_run_names
from test_run_isolation_witness import ACCOUNT, connected_types, run_one

import cairnwork_sandbox

pytestmark = pytest.mark.sandbox

WAIT = 5.0  # seconds the candidate keeps trying the sockets made after the policy
PROBE = (
    "import os, socket, sys, time\n\n{connector}\n\n"
    "def test_host_tmp_sockets_are_not_connectable():\n"
    "    if sys.platform == 'linux':  # Seatbelt denies every AF_UNIX connect (deny network*)\n"
    "        os.chdir(os.environ['TMPDIR'])  # a short relative name: AF_UNIX paths are capped\n"
    "        own = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "        own.bind('cw-own.sock')\n"
    "        own.listen(1)\n"
    "        assert connected_types('cw-own.sock') == ['SOCK_STREAM'], 'own socket unreachable'\n"
    "    reached, deadline = set(), time.monotonic() + {wait!r}\n"
    "    while time.monotonic() < deadline:\n"
    "        reached |= {{p for p in {paths!r} if connected_types(p)}}\n"
    "        time.sleep(0.2)\n"
    "    print('REACHED ' + repr(sorted(reached)))\n"
    "    assert not reached, 'host /tmp sockets connectable, listed on stdout'\n"
)


class Planter:
    """Host-side sockets under /tmp, each set in its own 0755 directory; closed and removed after.
    Mode 0777, so the verifier account could connect to them without the sandbox."""

    def __init__(self) -> None:
        self.socks: list[socket.socket] = []
        self.dirs: list[str] = []
        self.texts: list[str] = []  # every policy built while planted
        self.timer: threading.Timer | None = None

    @staticmethod
    def place(tag: str) -> str:
        return f"/tmp/cw-391-{tag}-{os.urandom(4).hex()}"  # noqa: S108 - the point of the test

    def plant(self, base: str, kinds=(socket.SOCK_STREAM,)) -> list[str]:
        self.dirs.append(base)
        os.mkdir(base)
        os.chmod(base, 0o755)
        paths = []
        for kind in kinds:
            path = f"{base}/{'s' if kind == socket.SOCK_STREAM else 'd'}"
            sock = socket.socket(socket.AF_UNIX, kind)
            self.socks.append(sock)
            sock.bind(path)
            if kind == socket.SOCK_STREAM:
                sock.listen(8)
            os.chmod(path, 0o777)
            paths.append(path)
        return paths

    def after_policy(self, text: str, late: list[str]) -> None:
        """The first policy is the run's: one socket now, one about a second later."""
        self.texts.append(text)
        if self.timer is None:
            self.plant(os.path.dirname(late[0]))
            self.timer = threading.Timer(1.0, self.plant, (os.path.dirname(late[1]),))
            self.timer.start()

    def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer.join()
        for sock in self.socks:
            sock.close()
        for base in self.dirs:
            shutil.rmtree(base, ignore_errors=True)


class AfterPolicy:
    """The real backend, except that building a policy calls ``made`` with its text."""

    def __init__(self, real, made) -> None:
        self.real, self.made = real, made

    def __getattr__(self, name):
        return getattr(self.real, name)

    def policy(self, *args, **kwargs):
        policy = self.real.policy(*args, **kwargs)
        self.made(policy.text)
        return policy


def tmp_socket_run(repo, tmp_path, monkeypatch, account=None, prefixes=None):
    planter = Planter()
    try:
        planted = planter.plant(planter.place("early"), (socket.SOCK_STREAM, socket.SOCK_DGRAM))
        late = [f"{planter.place(f'late{i}')}/s" for i in (1, 2)]
        real = cairnwork_sandbox.select

        def select():
            backend = real()
            made = lambda text: planter.after_policy(text, late)  # noqa: E731
            return backend and AfterPolicy(backend, made)

        monkeypatch.setattr(cairnwork_sandbox, "select", select)
        code = PROBE.format(
            connector=inspect.getsource(connected_types), wait=WAIT, paths=planted + late
        )
        files = {"tests/test_tmp_sockets.py": code}
        run = run_one(repo, tmp_path, files, "tests/test_tmp_sockets.py", account, prefixes)
        if planter.timer is not None:
            planter.timer.join()
        assert planter.texts, "no policy was built: the late sockets were never made"
        assert not [p for p in late if any(os.path.dirname(p) in t for t in planter.texts)]
        assert all(os.path.exists(p) for p in planted + late), "a socket was not made"
        assert run.observation.passed, "\n".join((run.why, run.log_path.read_text()))
    finally:
        planter.close()


def test_host_tmp_sockets_are_not_connectable_inside_a_run(repo, tmp_path, monkeypatch):
    tmp_socket_run(repo, tmp_path, monkeypatch)


@pytest.fixture
def own_names():
    """Name prefixes this test creates in the shared account run root (run_root_scope)."""
    with own_run_names(ACCOUNT.run_root, ACCOUNT) as prefixes:
        yield prefixes


@pytest.mark.verifier_account
def test_account_host_tmp_sockets_are_not_connectable(repo, tmp_path, monkeypatch, own_names):
    tmp_socket_run(repo, tmp_path, monkeypatch, ACCOUNT, own_names)
