"""Witnesses for two cross-boundary channels a verifier run may still have (#391, #392).

#391: the bwrap policy binds the host root read-only, and a read-only bind does not stop
``connect()`` on a pathname socket, and any socket under /run, /var/run or /tmp (journald's is mode
0666, the launching account's own ssh-agent or tmux socket) was reachable from a run.
The outer test lists the host's sockets (as the owner, before the run) and hands the list to a
candidate test, which also walks the same roots itself from inside the run and tries
``connect()`` on each with every AF_UNIX socket type. Nothing is sent. This is a point-in-time
witness: it covers the sockets present when it runs. On Linux the candidate first connects to a
socket of its own in TMPDIR, so a probe that cannot connect at all does not pass. Each run now
gets a private, read-only /run, /var/run and /tmp, so no host socket there is connectable in
either mode (planted and late sockets: test_tmp_isolation_witness.py). A further run checks what
the private trees must not break (writes, /etc/resolv.conf, user and locale lookups, a
multiprocessing child; in owner mode the run's own roots sit under /tmp and stay writable).

#392: in account mode, a run's private ``run/`` directory (and anything the candidate wrote
there) used to stay readable by the next run. The first run writes a marker in HOME, TMPDIR,
``run/`` and the work directory; after it returns, ``run/`` is gone and ``private`` is ``0700``.
The second run walks the account run root and opens those paths. It writes a file in its own
TMPDIR. On Linux the walk must see that file. On macOS Seatbelt refuses to list the run root,
which is recorded and is itself the witness that the walk was not skipped.

All of these run the real sandbox through run_verifiers. The account-mode variants need the
verifier account (pytest.mark.verifier_account).
"""

import hashlib
import inspect
import os
import socket
import stat
import sys
from pathlib import Path

import pytest
from conftest import commit_change
from run_root_scope import own_run_id, own_run_names
from socket_inventory import owner_sockets

import cairnwork_sandbox
from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

pytestmark = pytest.mark.sandbox

SOCKET_ROOTS = ("/run", "/var/run", "/tmp")  # noqa: S108 - only listed and probed
WALK_LIMIT, WALK_DEPTH = 100_000, 4  # daemon sockets sit 1-3 levels down (/run/systemd/journal)
ACCOUNT = VerifierAccount()


def socket_paths(roots, limit, depth):
    """(sockets, unreadable dirs, blind dirs, entries seen) under roots, at most ``depth`` levels
    down: no link followed, each root once by real path, sockets de-duplicated by real path. Raises
    when more than ``limit`` entries were seen, so a partial list never passes for a complete one.
    Blind: unreadable yet searchable, so connect() may reach a socket never probed (#418)."""
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
    blind = [p for p in unreadable if os.path.isdir(p) and os.access(p, os.X_OK)]
    return sorted(found), sorted(unreadable), sorted(blind), count


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
    "import os, socket, stat, sys\n"
    "from pathlib import Path\n\n"
    "{walker}\n\n{connector}\n\n"
    "HOST = {host!r}\n\n"
    "def own_socket_connects():\n"
    "    # a short relative name in TMPDIR: AF_UNIX paths are capped at 108 bytes\n"
    "    here = os.getcwd()\n"
    "    os.chdir(os.environ['TMPDIR'])\n"
    "    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
    "    try:\n"
    "        server.bind('cw-own.sock')\n"
    "        server.listen(1)\n"
    "        return connected_types('cw-own.sock')\n"
    "    finally:\n"
    "        server.close()\n"
    "        os.chdir(here)\n\n"
    "def test_no_host_socket_is_connectable():\n"
    "    if sys.platform == 'linux':  # Seatbelt denies every AF_UNIX connect (deny network*)\n"
    "        assert own_socket_connects() == ['SOCK_STREAM'], 'cannot connect to its own socket'\n"
    "    # the run root (owner mode: under /tmp) holds only this run's own directories\n"
    "    own = str(Path(os.environ['HOME']).resolve().parents[2])\n"
    "    inside, unreadable, blind, count = socket_paths({roots!r}, {limit!r}, {depth!r})\n"
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
    "          f'{{len(inside)}} in {{count}} entries, {{len(unreadable)}} unreadable, '\n"
    "          f'{{len(blind)}} searchable but unlistable)')\n"
    "    for line in reached:  # repr: a host path cannot start a line of its own in the log\n"
    "        print('CONNECTABLE ' + repr(line))\n"
    "    for path in unreadable:  # only a searchable one can hide a reachable socket\n"
    "        print(('BLIND ' if path in blind else 'UNREADABLE ') + repr(path))\n"
    "    assert not reached, f'{{len(reached)}} host sockets connectable, listed on stdout'\n"
    "    if sys.platform == 'linux':  # under Seatbelt no connect succeeds, hidden or not\n"
    "        assert not blind, f'{{len(blind)}} searchable dirs not listed, so never probed'\n"
)


def run_one(repo, tmp_path, files, target, account=None, prefixes=None):
    head = commit_change(repo, files)
    spec = parse_verifier("v", ["python3", "-m", "pytest", target])
    if account is None:
        root, python = tmp_path / "runs", sys.executable
    else:
        root, python = account.run_root, None
    # in account mode the run root is shared: register the id so only this run is removed
    run_id = "r" + os.urandom(6).hex() if prefixes is None else own_run_id(prefixes, "r")
    (run,) = run_verifiers(
        VerifyContext(repo, root, python=python, account=account), head, (spec,), run_id
    )
    return run


def socket_run(repo, tmp_path, account=None, prefixes=None):
    host, unreadable, blind, count = socket_paths(SOCKET_ROOTS, WALK_LIMIT, WALK_DEPTH)
    host, failures = owner_sockets(host, blind)
    for path in sorted(set(unreadable) - set(blind)):  # cannot be searched: nothing reachable
        print("UNREADABLE " + repr(path))
    for line in failures:  # blind dirs only an evidenced inventory may excuse (#418)
        print(line)
    assert not failures, f"{len(failures)} blind dirs or unlisted sockets, listed on stdout"
    assert host, f"no pathname socket under {SOCKET_ROOTS} on this host: the witness is vacuous"
    code = PROBE.format(
        walker=inspect.getsource(socket_paths),
        connector=inspect.getsource(connected_types),
        host=host,
        roots=SOCKET_ROOTS,
        limit=WALK_LIMIT,
        depth=WALK_DEPTH,
    )
    run = run_one(
        repo, tmp_path, {"tests/test_sockets.py": code}, "tests/test_sockets.py", account, prefixes
    )
    listed = f"host: {len(host)} sockets in {count} entries, {len(unreadable)} unreadable"
    return run, listed


def test_no_host_pathname_socket_is_connectable_inside_a_run(repo, tmp_path):
    run, listed = socket_run(repo, tmp_path)
    assert run.observation.passed, "\n".join((run.why, listed, run.log_path.read_text()))


# --- the same under the separate verifier account -------------------------------------------


@pytest.fixture
def own_names():
    """Name prefixes these tests create in the account's run root; only those are removed after
    the test, under the account lock (run_root_scope). The run root is shared with other
    sessions' runs: never remove "whatever is new"."""
    with own_run_names(ACCOUNT.run_root, ACCOUNT) as prefixes:
        yield prefixes


@pytest.mark.verifier_account
def test_account_no_host_pathname_socket_is_connectable(repo, tmp_path, own_names):
    run, listed = socket_run(repo, tmp_path, account=ACCOUNT, prefixes=own_names)
    assert run.observation.passed, "\n".join((run.why, listed, run.log_path.read_text()))


# What the private trees (#391) must leave working, under bwrap: writes to the run's own places
# (pytest's tmp_path and tempfile's directory are in TMPDIR) and none outside, /etc/resolv.conf
# (a link into /run on systemd hosts), name, user and locale lookups, and a multiprocessing child
# (its queue's lock lives in /dev/shm). In owner mode the run root is under /tmp, so the writes
# also show that bwrap's non-recursive --remount-ro of /tmp leaves the write binds writable (O1).
WORKS = (
    "import hashlib, locale, os, pwd, socket, tempfile\n"
    "from multiprocessing import Process, Queue\n\n"
    "def put(queue):\n"
    "    queue.put('cw-mp-ok')\n\n"
    "def test_a_run_still_works(tmp_path):\n"
    "    work = os.path.dirname(os.getcwd())  # next to the checkout (the checkout is compared)\n"
    "    places = (work, os.environ['TMPDIR'], os.environ['HOME'], tempfile.gettempdir())\n"
    "    for where in (*places, str(tmp_path)):\n"
    "        with open(os.path.join(where, 'cw-391-write'), 'w') as handle:\n"
    "            handle.write('x')\n"
    "    for where in {readonly!r}:\n"
    "        try:\n"
    "            open(os.path.join(where, 'cw-391-write'), 'w').close()\n"
    "        except OSError:\n"
    "            continue\n"
    "        raise AssertionError('wrote under ' + where)\n"
    "    if {resolv!r}:\n"
    "        data = open('/etc/resolv.conf', 'rb').read()\n"
    "        assert hashlib.sha256(data).hexdigest() == {resolv!r}\n"
    "    assert socket.getaddrinfo('localhost', None)\n"
    "    locale.setlocale(locale.LC_ALL, 'C.UTF-8')\n"
    "    assert pwd.getpwuid(os.getuid()).pw_uid == os.getuid()\n"
    "    queue = Queue()\n"
    "    child = Process(target=put, args=(queue,))\n"
    "    child.start()\n"
    "    assert queue.get(timeout=10) == 'cw-mp-ok'\n"
    "    child.join(timeout=10)\n"
    "    assert child.exitcode == 0\n"
)


def works_run(repo, tmp_path, account=None, prefixes=None):
    backend = cairnwork_sandbox.platform_backend()
    if backend is None or backend.name != "bwrap":
        pytest.skip("bwrap only: the private trees are bwrap mounts (#391)")
    try:  # the host's view; a file the owner cannot read is not checked inside
        resolv = hashlib.sha256(Path("/etc/resolv.conf").read_bytes()).hexdigest()
    except OSError:
        resolv = ""
    readonly = [d for d in ("/run", "/var/run", "/tmp") if os.path.isdir(d)]  # noqa: S108
    code = WORKS.format(readonly=readonly, resolv=resolv)
    files = {"tests/test_works.py": code}
    run = run_one(repo, tmp_path, files, "tests/test_works.py", account, prefixes)
    assert run.observation.passed, "\n".join((run.why, run.log_path.read_text()))


def test_a_run_with_a_private_run_tree_still_works(repo, tmp_path):
    works_run(repo, tmp_path)


@pytest.mark.verifier_account
def test_account_a_run_with_a_private_run_tree_still_works(repo, tmp_path, own_names):
    works_run(repo, tmp_path, ACCOUNT, own_names)


# #392: markers the first run writes. home/tmp/run sit under private/run and are removed with it;
# work sits in <run_id>.work, which remove_checkout deletes. The known paths are that formula.
PLACES = {
    "home": "Path(os.environ['HOME'])",
    "tmpdir": "Path(os.environ['TMPDIR'])",
    "run": "Path(os.environ['HOME']).parent",
    "work": "Path.cwd().parent",
}
LEAVE = (
    "import os\nfrom pathlib import Path\n\n"
    "def test_leave():\n"
    "    written = []\n"
    "    for where, place in {places}.items():\n"
    "        path = place / ('cw-leftover-{token}-' + where)\n"
    "        path.write_text('{token}')\n"
    "        written.append(where)\n"
    "    assert written == ['home', 'tmpdir', 'run', 'work']\n"
)
FIND = (
    "import errno, os, sys\n\n"
    "def test_nothing_of_the_last_run_is_left():\n"
    "    # TMPDIR is this run's write root, which the sandbox may read.\n"
    "    control = os.path.join(os.environ['TMPDIR'], 'cw-walk-control')\n"
    "    with open(control, 'w') as handle:\n"
    "        handle.write({control_text!r})\n"
    "    found, readable, unreadable, count, saw = [], [], [], 0, False\n"
    "    def note(error):\n"
    "        unreadable.append((error.filename, error.errno))\n"
    "    root = {root!r}\n"
    "    for top, dirs, files in os.walk(root, onerror=note):\n"
    "        count += len(dirs) + len(files)\n"
    "        assert count <= {limit!r}, 'too many entries under the run root'\n"
    "        here = os.path.join(top, 'cw-walk-control')\n"
    "        saw = saw or (here == control and 'cw-walk-control' in files)\n"
    "        found += [os.path.join(top, n) for n in dirs + files if '{token}' in n]\n"
    "    for path in sorted(set(found) | set({known!r})):\n"
    "        try:\n"
    "            with open(path) as f:\n"
    "                if f.read() == '{token}':\n"
    "                    readable.append(path)\n"
    "        except OSError:\n"
    "            pass\n"
    "    def same(path):\n"
    "        return path is not None and os.path.normpath(path) == os.path.normpath(root)\n"
    "    # ENOENT is not a sandbox refusal. Only EPERM or EACCES on the root itself is.\n"
    "    denied = (errno.EPERM, errno.EACCES)\n"
    "    blocked = any(same(path) and err in denied for path, err in unreadable)\n"
    "    reached = saw and count > 0\n"
    "    # Linux lists the run root, so the walk must see this run. macOS Seatbelt refuses\n"
    "    # the listing; that refusal is the witness, and the marker opens below still run.\n"
    "    ok = reached if sys.platform != 'darwin' else (reached or blocked)\n"
    "    print(f'{{count}} entries walked, control seen {{saw}}, root blocked {{blocked}}')\n"
    "    for path, err in unreadable:\n"
    "        print('UNREADABLE ' + str(path) + ' errno ' + str(err))\n"
    "    for path in found:\n"
    "        print('LEFTOVER FOUND ' + path)\n"
    "    for path in readable:\n"
    "        print('LEFTOVER READABLE ' + path)\n"
    "    assert open(control).read() == {control_text!r}, 'walk control was not readable'\n"
    "    detail = 'unreadable ' + str(unreadable)\n"
    "    assert ok, 'the walk did not reach this run; ' + detail\n"
    "    assert not found and not readable, 'leftovers of the last run, listed on stdout'\n"
)


def leftovers(root, token):
    """Owner's view. An unreadable directory fails the walk: silence is not 'nothing left'."""
    hits = []

    def boom(err):
        raise RuntimeError(f"cannot walk {err.filename}") from err

    for top, dirs, files in os.walk(root, onerror=boom):
        hits += [os.path.join(top, n) for n in dirs + files if token in n]
    return sorted(hits)


def _marker_paths(private, run_root, token):
    """Where LEAVE writes. mkdtemp keeps checkout.name before the dot in the private dir's name."""
    stem, name = private.name.rsplit(".", 1)
    assert len(name) == 8  # mkdtemp suffix; a different shape means the formula is wrong
    run = private / "run"
    return [
        str(run / "home" / f"cw-leftover-{token}-home"),
        str(run / "tmp" / f"cw-leftover-{token}-tmpdir"),
        str(run / f"cw-leftover-{token}-run"),
        str(run_root / f"{stem}.work" / f"cw-leftover-{token}-work"),
    ]


@pytest.mark.verifier_account
def test_account_a_run_cannot_find_what_the_last_run_left(repo, tmp_path, own_names):
    token = os.urandom(8).hex()
    places = "{" + ", ".join(f"{k!r}: {v}" for k, v in PLACES.items()) + "}"
    first = run_one(
        repo, tmp_path, {"tests/test_leave.py": LEAVE.format(places=places, token=token)},
        "tests/test_leave.py", ACCOUNT, own_names,
    )  # fmt: skip
    if not first.observation.passed:
        pytest.fail(first.why + "\n" + first.log_path.read_text())
    private = first.log_path.parent
    if os.path.lexists(private / "run"):
        pytest.fail(f"{private / 'run'} still exists after the run")
    mode = stat.S_IMODE(private.stat().st_mode)
    if mode != 0o700:
        pytest.fail(f"{private} mode is {mode:04o}, want 0700")
    for name in ("verifier.log", "pytest.ini", "profile.sb"):
        if not (private / name).is_file():
            pytest.fail(f"evidence {private / name} was removed with run/")
    if first.observation.artifact_id != f"{private.name}/verifier.log":
        pytest.fail(f"observation log path is {first.observation.artifact_id}")
    known = _marker_paths(private, ACCOUNT.run_root, token)
    still = leftovers(ACCOUNT.run_root, token)
    if still:
        pytest.fail(f"owner can still see the last run's markers: {still}")
    code = FIND.format(
        root=str(ACCOUNT.run_root), limit=WALK_LIMIT, token=token, known=known,
        control_text="control-ok",
    )  # fmt: skip
    second = run_one(
        repo, tmp_path, {"tests/test_find.py": code}, "tests/test_find.py", ACCOUNT, own_names
    )
    if not second.observation.passed:
        pytest.fail(second.why + "\n" + second.log_path.read_text())
