"""Witnesses for two cross-boundary channels a verifier run may still have (#391, #392).

#391: the bwrap policy binds the host root read-only, and a read-only bind does not stop
``connect()`` on a pathname socket. Only a named set of daemon sockets and a few socket trees are
masked; any other socket under /run, /var/run or /tmp (journald's is mode 0666) may stay reachable.
The outer test lists the host's sockets (as the owner, before the run) and hands the list to a
candidate test, which also walks the same roots itself from inside the run and tries
``connect()`` on each with every AF_UNIX socket type. Nothing is sent. This is a point-in-time
witness: it covers the sockets present when it runs.

#392: in account mode, files a run leaves in its private ``run/`` directory (HOME and TMPDIR are
removed after the run, the checkout's work directory too, but ``run/`` itself is not) may stay
readable by the next run under the same account. The first run writes a marker next to each of
those places; the second looks for it, by walking the account run root and by opening the known
paths.

All of these run the real sandbox through run_verifiers. The account-mode variants need the
verifier account (pytest.mark.verifier_account).
"""

import inspect
import os
import shutil
import socket
import stat
import sys

import pytest
from conftest import commit_change

from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import parse_verifier, run_verifiers

pytestmark = pytest.mark.sandbox

SOCKET_ROOTS = ("/run", "/var/run", "/tmp")  # noqa: S108 - only listed and probed
WALK_LIMIT, WALK_DEPTH = 100_000, 4  # daemon sockets sit 1-3 levels down (/run/systemd/journal)
ACCOUNT = VerifierAccount()


def socket_paths(roots, limit, depth):
    """(sockets, unreadable dirs, entries seen) under roots, at most ``depth`` levels down: no link
    followed, each root once by real path, sockets de-duplicated by real path. Raises when more
    than ``limit`` entries were seen, so a partial list never passes for a complete one."""
    found, unreadable, seen, count = set(), [], set(), 0
    for root in roots:
        real = os.path.realpath(root)
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        for top, dirs, files in os.walk(real, onerror=lambda e: unreadable.append(e.filename)):
            if top.count("/") - real.count("/") >= depth:
                dirs[:] = []
            count += len(dirs) + len(files)
            if count > limit:
                raise RuntimeError(f"more than {limit} entries under {roots}: raise the limit")
            for name in files:
                path = os.path.join(top, name)
                try:
                    if stat.S_ISSOCK(os.lstat(path).st_mode):
                        found.add(os.path.realpath(path))
                except OSError:
                    unreadable.append(path)
    return sorted(found), sorted(unreadable), count


def connected_types(path):
    """The AF_UNIX socket types for which connect() on ``path`` succeeds."""
    kinds = []
    for name in ("SOCK_STREAM", "SOCK_DGRAM", "SOCK_SEQPACKET"):
        kind = getattr(socket, name, None)
        if kind is None:
            continue
        try:
            sock = socket.socket(socket.AF_UNIX, kind)
        except OSError:  # a type this host does not offer for AF_UNIX
            continue
        sock.settimeout(1)
        try:
            sock.connect(path)
            kinds.append(name)
        except OSError:
            pass
        finally:
            sock.close()
    return kinds


PROBE = (
    "import os, socket, stat\n"
    "from pathlib import Path\n\n"
    "{walker}\n\n{connector}\n\n"
    "HOST = {host!r}\n\n"
    "def test_no_host_socket_is_connectable():\n"
    "    # the run root (owner mode: under /tmp) holds only this run's own directories\n"
    "    own = str(Path(os.environ['HOME']).resolve().parents[2])\n"
    "    inside, unreadable, count = socket_paths({roots!r}, {limit!r}, {depth!r})\n"
    "    probed = sorted(set(HOST) | set(inside))\n"
    "    reached = []\n"
    "    for path in probed:\n"
    "        if path == own or path.startswith(own + '/'):\n"
    "            continue\n"
    "        kinds = connected_types(path)\n"
    "        if kinds:\n"
    "            reached.append(path + ' (' + ','.join(kinds) + ')')\n"
    "    # printed, not put in the message: pytest cuts a long assertion message short\n"
    "    print(f'probed {{len(probed)}} (host listed {{len(HOST)}}, the run found '\n"
    "          f'{{len(inside)}} in {{count}} entries, {{len(unreadable)}} unreadable)')\n"
    "    for line in reached:\n"
    "        print('CONNECTABLE ' + line)\n"
    "    assert not reached, f'{{len(reached)}} host sockets connectable, listed on stdout'\n"
)


def run_one(repo, tmp_path, files, target, account=None):
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", target])
    if account is None:
        root, python = tmp_path / "runs", sys.executable
    else:
        root, python = account.run_root, None
    run_id = "r" + os.urandom(6).hex()
    (run,) = run_verifiers(repo, head, (spec,), root, run_id, python=python, account=account)
    return run


def socket_run(repo, tmp_path, account=None):
    host, unreadable, count = socket_paths(SOCKET_ROOTS, WALK_LIMIT, WALK_DEPTH)
    assert host, f"no pathname socket under {SOCKET_ROOTS} on this host: the witness is vacuous"
    code = PROBE.format(
        walker=inspect.getsource(socket_paths),
        connector=inspect.getsource(connected_types),
        host=host,
        roots=SOCKET_ROOTS,
        limit=WALK_LIMIT,
        depth=WALK_DEPTH,
    )
    run = run_one(repo, tmp_path, {"tests/test_sockets.py": code}, "tests/test_sockets.py", account)
    listed = f"host: {len(host)} sockets in {count} entries, {len(unreadable)} unreadable"
    return run, listed


def test_no_host_pathname_socket_is_connectable_inside_a_run(repo, tmp_path):
    run, listed = socket_run(repo, tmp_path)
    assert run.observation.passed, "\n".join((run.why, listed, run.log_path.read_text()))


# --- the same under the separate verifier account -------------------------------------------


@pytest.fixture
def account_run_root():
    """The account's run root is shared: remove what these tests leave there."""
    root = ACCOUNT.run_root
    before = set(os.listdir(root)) if root.is_dir() else set()
    yield
    for name in (set(os.listdir(root)) if root.is_dir() else set()) - before:
        ACCOUNT.remove(root / name)
        shutil.rmtree(root / name, ignore_errors=True)


@pytest.mark.verifier_account
def test_account_no_host_pathname_socket_is_connectable(repo, tmp_path, account_run_root):
    run, listed = socket_run(repo, tmp_path, account=ACCOUNT)
    assert run.observation.passed, "\n".join((run.why, listed, run.log_path.read_text()))


# #392: where the first run leaves a marker. HOME, TMPDIR and the work directory are removed after
# every run; run/ (HOME's parent) is not.
PLACES = {
    "home": "Path(os.environ['HOME'])",
    "tmpdir": "Path(os.environ['TMPDIR'])",
    "run": "Path(os.environ['HOME']).parent",
    "work": "Path.cwd().parent",
}
LEAVE = (
    "import os\nfrom pathlib import Path\n\n"
    "def test_leave():\n"
    "    for where, place in {places}.items():\n"
    "        (place / ('cw-leftover-{token}-' + where)).write_text('{token}')\n"
)
FIND = (
    "import os\n\n"
    "def test_nothing_of_the_last_run_is_left():\n"
    "    found, readable, unreadable, count = [], [], [], 0\n"
    "    def note(error):\n"
    "        unreadable.append(error.filename)\n"
    "    for top, dirs, files in os.walk({root!r}, onerror=note):\n"
    "        count += len(dirs) + len(files)\n"
    "        assert count <= {limit!r}, 'too many entries under the run root'\n"
    "        found += [os.path.join(top, n) for n in dirs + files if '{token}' in n]\n"
    "    for path in sorted(set(found) | set({known!r})):\n"
    "        try:\n"
    "            with open(path) as f:\n"
    "                if f.read() == '{token}':\n"
    "                    readable.append(path)\n"
    "        except OSError:\n"
    "            pass\n"
    "    print(f'{{count}} entries walked, {{len(unreadable)}} unreadable')\n"
    "    for path in found:\n"
    "        print('LEFTOVER FOUND ' + path)\n"
    "    for path in readable:\n"
    "        print('LEFTOVER READABLE ' + path)\n"
    "    assert not found and not readable, 'leftovers of the last run, listed on stdout'\n"
)


def leftovers(root, token):
    """Owner's view: the paths under ``root`` whose name carries ``token`` (no link followed)."""
    hits = []
    for top, dirs, files in os.walk(root):
        hits += [os.path.join(top, n) for n in dirs + files if token in n]
    return sorted(hits)


@pytest.mark.verifier_account
def test_account_a_run_cannot_find_what_the_last_run_left(repo, tmp_path, account_run_root):
    token = os.urandom(8).hex()
    places = "{" + ", ".join(f"{k!r}: {v}" for k, v in PLACES.items()) + "}"
    first = run_one(
        repo, tmp_path, {"tests/test_leave.py": LEAVE.format(places=places, token=token)},
        "tests/test_leave.py", ACCOUNT,
    )  # fmt: skip
    assert first.observation.passed, first.why + "\n" + first.log_path.read_text()
    run_dir = first.log_path.parent / "run"  # the private dir holds the log and run/
    known = [str(run_dir / f"cw-leftover-{token}-{where}") for where in PLACES]
    owner_view = leftovers(ACCOUNT.run_root, token)
    code = FIND.format(root=str(ACCOUNT.run_root), limit=WALK_LIMIT, token=token, known=known)
    second = run_one(repo, tmp_path, {"tests/test_find.py": code}, "tests/test_find.py", ACCOUNT)
    owner_lines = "".join(f"LEFT AFTER RUN 1 (owner's view) {path}\n" for path in owner_view)
    assert second.observation.passed, second.why + "\n" + owner_lines + second.log_path.read_text()
