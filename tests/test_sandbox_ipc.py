"""#311: named IPC cannot carry a value into the blackbox run.

The blackbox run's policy (``allow_ipc=False``) denies named POSIX semaphores, POSIX shared memory
and SysV IPC, and judge_candidate runs the blackbox before any other run of the candidate in the
same verify. The policy witnesses create objects outside the sandbox, as a previous run would have
left them, and try to reach them from inside. The end-to-end attacks go through cairnwork-verify
as the owner (no verifier account): a declared check never passes there, but ``[matched k/n]``
says whether the carried value reached the function's result.

What stays open on macOS (#316; pinned below, documented in docs/SECURITY.md): whether a name or key
exists is visible from inside (ENOENT for an absent name, EPERM for a present one), because these
namespaces are global and the lookup happens before the sandbox is asked. Under bwrap every run has
its own IPC namespace and /dev, so nothing left outside is visible at all.
"""

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from verify_helpers import base_of, candidate, verify

import cairnwork_sandbox
from cairnwork import pytest_config

_mp: Any = __import__("_multiprocessing")  # untyped C modules: no stubs for pyright
_shm: Any = __import__("_posixshmem")

PROBE = r"""
import ctypes, errno, os, sys
op, name = sys.argv[1], sys.argv[2]
libc = ctypes.CDLL(None, use_errno=True)
libc.sem_open.restype = ctypes.c_void_p
libc.sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
def result(ok):
    print("ok" if ok else errno.errorcode.get(ctypes.get_errno(), "E?"))
if op == "sem":
    handle = libc.sem_open(name.encode(), 0)
    result(handle not in (None, ctypes.c_void_p(-1).value))
elif op == "create-sem":
    import _multiprocessing
    try:
        _multiprocessing.SemLock(1, 1, 1, name, 1)
        print("ok")
    except OSError as exc:
        print(errno.errorcode.get(exc.errno, "E?"))
elif op == "shm":
    import _posixshmem
    try:
        os.close(_posixshmem.shm_open(name, os.O_RDWR, 0))
        print("ok")
    except OSError as exc:
        print(errno.errorcode.get(exc.errno, "E?"))
else:  # SysV: open an existing object by key (flags 0)
    key = int(name)
    libc.semget.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
    libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
    for call in (lambda: libc.semget(key, 0, 0), lambda: libc.shmget(key + 1, 0, 0),
                 lambda: libc.msgget(key + 2, 0)):
        ctypes.set_errno(0)
        result(call() >= 0)
"""


def _confined(tmp_path: Path, allow_ipc: bool, *args: str) -> list[str]:
    backend = cairnwork_sandbox.select()
    assert backend is not None
    scratch = (tmp_path / f"run-{allow_ipc}").resolve()
    scratch.mkdir(exist_ok=True)
    probe = scratch / "probe.py"
    probe.write_text(PROBE)
    policy = backend.policy(
        read_roots=(*pytest_config.interpreter_roots(sys.executable), scratch),
        write_roots=(scratch,),
        allow_ipc=allow_ipc,
    )
    path = scratch / "policy"
    path.write_text(policy.text)
    argv = backend.wrap([sys.executable, "-I", str(probe), *args], path)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    return done.stdout.split() or [f"rc={done.returncode}:{done.stderr[-200:]}"]


def _host(tmp_path: Path, *args: str) -> list[str]:
    """The same probe outside any sandbox: the control that the probe itself works."""
    probe = tmp_path / "host-probe.py"
    probe.write_text(PROBE)
    done = subprocess.run(
        [sys.executable, "-I", str(probe), *args], capture_output=True, text=True, timeout=60,
        check=False,
    )  # fmt: skip
    return done.stdout.split() or [f"rc={done.returncode}:{done.stderr[-200:]}"]


def _seatbelt() -> bool:
    backend = cairnwork_sandbox.platform_backend()
    return backend is not None and backend.name == "seatbelt"


def _libc() -> Any:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.semget.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    libc.shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
    libc.msgget.argtypes = [ctypes.c_int, ctypes.c_int]
    return libc


@pytest.fixture
def leftovers():
    """Named objects of every kind, made outside any sandbox (as a previous run leaves them)."""
    token = secrets.token_hex(4)
    names = {"mp": f"/mp-cw{token}", "any": f"/cw{token}"}
    shm = f"/cwshm{token}"
    key = 0x30000000 + secrets.randbelow(0x0FFFFFF0)
    for name in names.values():
        _mp.SemLock(1, 5, 100, name, 0)
    os.close(_shm.shm_open(shm, os.O_CREAT | os.O_RDWR, 0o600))
    libc = _libc()
    ids = (
        libc.semget(key, 1, 0o1600),
        libc.shmget(key + 1, 4096, 0o1600),
        libc.msgget(key + 2, 0o1600),
    )
    assert min(ids) >= 0, ids
    try:
        yield names, shm, key
    finally:
        for name in names.values():
            _mp.sem_unlink(name)
        _shm.shm_unlink(shm)
        ipcrm = shutil.which("ipcrm")
        if ipcrm:
            for flag, ident in zip(("-s", "-m", "-q"), ids, strict=True):
                subprocess.run([ipcrm, flag, str(ident)], capture_output=True, check=False)


@pytest.mark.sandbox
def test_the_blackbox_policy_reaches_no_leftover(tmp_path, leftovers):
    """Seatbelt refuses each one (EPERM); under bwrap none of them is even there (ENOENT: its own
    IPC namespace and /dev). The controls: the probe opens all of them outside the sandbox, and on
    Seatbelt the verifier policy, which differs only in allow_ipc, opens the /mp- one."""
    names, shm, key = leftovers
    for name in names.values():
        assert _host(tmp_path, "sem", name) == ["ok"], name
    assert _host(tmp_path, "shm", shm) == ["ok"]
    assert _host(tmp_path, "sysv", str(key)) == ["ok"] * 3
    denied = "EPERM" if _seatbelt() else "ENOENT"
    for name in names.values():
        assert _confined(tmp_path, False, "sem", name) == [denied], name
    assert _confined(tmp_path, False, "shm", shm) == [denied]
    assert _confined(tmp_path, False, "sysv", str(key)) == [denied] * 3
    if _seatbelt():
        assert _confined(tmp_path, True, "sem", names["mp"]) == ["ok"]


@pytest.mark.sandbox
def test_the_blackbox_policy_cannot_create_a_named_semaphore(tmp_path):
    """Seatbelt only: under bwrap a semaphore made in the run lives in the run's own /dev/shm and
    dies with it, so creating one there is harmless."""
    if not _seatbelt():
        pytest.skip("Seatbelt witness")
    token = secrets.token_hex(4)
    assert _confined(tmp_path, True, "create-sem", f"/mp-cw{token}") == ["ok"]  # the control
    for name in (f"/mp-cw{token}", f"/cw{token}"):
        assert _confined(tmp_path, False, "create-sem", name) == ["EPERM"], name


@pytest.mark.sandbox
def test_whether_a_leftover_name_exists_is_still_visible_on_macos(tmp_path, leftovers):
    """The known macOS residual, pinned so a change in it is noticed: EPERM for a present name,
    ENOENT for an absent one. Under bwrap a name left outside is simply absent."""
    names, _, _ = leftovers
    absent = _confined(tmp_path, False, "sem", f"/mp-cw{secrets.token_hex(4)}")
    present = _confined(tmp_path, False, "sem", names["mp"])
    assert absent == ["ENOENT"]
    backend = cairnwork_sandbox.platform_backend()
    seatbelt = backend is not None and backend.name == "seatbelt"
    assert present == (["EPERM"] if seatbelt else ["ENOENT"]), present


# End to end. The candidate's add() returns 5 for (2, 3) only if a semaphore told it so; otherwise
# it returns a wrong value. ``WRITE`` (in the pytest phase) leaves the answer as a semaphore's
# count, under an arbitrary name and under multiprocessing's /mp- prefix; ``READ`` (in the blackbox
# phase) opens either name and counts it down, and ``PROBE_EXISTS`` only asks whether one exists.
CASE = [{"id": "small", "args": [2, 3], "expected": 5}]
_HEAD = (
    "import ctypes, errno, os, re, sys\n"
    "_NAMES = ['/mp-cw{token}', '/cw{token}']\n"
    "_BLACKBOX = sys.argv[0].endswith('_cairnwork_blackbox_runner.py')\n"
    "def _libc():\n"
    "    libc = ctypes.CDLL(None, use_errno=True)\n"
    "    libc.sem_open.restype = ctypes.c_void_p\n"
    "    libc.sem_open.argtypes = [ctypes.c_char_p, ctypes.c_int]\n"
    "    libc.sem_trywait.argtypes = [ctypes.c_void_p]\n"
    "    return libc\n"
    "def _open(libc, name):\n"
    "    handle = libc.sem_open(name.encode(), 0)\n"
    "    return None if handle in (None, ctypes.c_void_p(-1).value) else handle\n"
)
WRITE = (
    "if not _BLACKBOX:  # the pytest phase: it has seen the tests, so it knows the answer\n"
    "    import _multiprocessing\n"
    "    _test = os.path.join(os.path.dirname(__file__), '..', 'tests', 'test_calc.py')\n"
    "    _answer = int(re.search(r'== (\\d+)', open(_test).read()).group(1))\n"
    "    for _n in _NAMES:\n"
    "        try:\n"
    "            _multiprocessing.SemLock(1, _answer, 100, _n, 0)\n"
    "        except OSError:\n"
    "            pass\n"
)
READ = (  # only in the blackbox phase: counting down in the pytest phase would empty it first
    "def add(a, b):\n"
    "    libc = _libc()\n"
    "    if not _BLACKBOX:\n"
    "        return a + b + 1\n"
    "    for name in _NAMES:\n"
    "        handle = _open(libc, name)\n"
    "        count = 0\n"
    "        while handle is not None and count < 100 and libc.sem_trywait(handle) == 0:\n"
    "            count += 1\n"
    "        if count:\n"
    "            return count\n"
    "    return a + b + 1\n"
)
PROBE_EXISTS = (
    "def add(a, b):\n"
    "    libc = _libc()\n"
    "    for name in _NAMES:\n"
    "        if _open(libc, name) is not None or ctypes.get_errno() != errno.ENOENT:\n"
    "            return a + b\n"
    "    return a + b + 1\n"
)


def _source(token: str, *parts: str) -> str:
    return _HEAD.format(token=token) + "".join(parts)


def _declaration(tmp_path: Path) -> str:
    from verify_helpers import declaration

    return str(declaration(tmp_path, cases=CASE))


def _verify(state, project, source: str, declared: str) -> dict:
    base = base_of(project)
    sha = candidate(project, base, {"src/calc.py": source})
    return verify(state, project, base, sha, "--import-path", "src", "--blackbox", declared)[1]


def _matched(out: dict) -> str:
    assert out["status"] != "VERIFIED" and not out["verified"], out
    reason = out["reason"]
    assert "blackbox:add: " in reason, reason
    return reason.split("blackbox:add: ", 1)[1]


@pytest.fixture
def token():
    value = secrets.token_hex(4)
    yield value
    for name in (f"/mp-cw{value}", f"/cw{value}"):
        try:
            _mp.sem_unlink(name)
        except OSError:
            pass


@pytest.mark.sandbox
def test_the_pytest_phase_of_the_same_verify_cannot_reach_the_blackbox(
    state, project, tmp_path, token
):
    """(1) Written in the pytest phase, read in the blackbox phase of the same verify, with the
    strongest reader (existence alone): the blackbox has run before the pytest phase writes."""
    out = _verify(state, project, _source(token, WRITE, PROBE_EXISTS), _declaration(tmp_path))
    assert "[matched 0/1]" in _matched(out), out["reason"]
    if _seatbelt():  # the write did happen (under bwrap it died with the run's /dev/shm)
        assert _host(tmp_path, "sem", f"/mp-cw{token}") == ["ok"]


@pytest.mark.sandbox
def test_a_semaphore_left_by_a_previous_run_carries_no_value(state, project, tmp_path, token):
    """(2) A leftover holding the answer as its count, under both names, made outside the sandbox
    before this verify: the blackbox run cannot open it."""
    for name in (f"/mp-cw{token}", f"/cw{token}"):
        _mp.SemLock(1, 5, 100, name, 0)
    out = _verify(state, project, _source(token, READ), _declaration(tmp_path))
    assert "[matched 0/1]" in _matched(out), out["reason"]


@pytest.mark.sandbox
def test_a_value_written_in_one_verify_does_not_reach_the_next(state, project, tmp_path, token):
    """(3) Cross-run: the first verify's pytest phase leaves the answer it read in the tests; a
    second verify of another candidate reads it in its blackbox phase. It cannot open it."""
    declared = _declaration(tmp_path)
    _verify(
        state, project, _source(token, WRITE, "def add(a, b):\n    return a + b + 1\n"), declared
    )
    out = _verify(state, project, _source(token, READ), declared)
    assert "[matched 0/1]" in _matched(out), out["reason"]


def test_only_the_blackbox_policy_changes(tmp_path):
    """Verifier runs keep their profile byte for byte; bwrap ignores the option because each run
    already has its own IPC namespace (--unshare-all) and a fresh /dev (--dev)."""
    from cairnwork_sandbox import bwrap, seatbelt

    roots, writes = (tmp_path.resolve(),), ((tmp_path / "w").resolve(),)
    default = seatbelt.BACKEND.policy(roots, writes).text
    assert seatbelt.BACKEND.policy(roots, writes, allow_ipc=True).text == default
    closed = seatbelt.BACKEND.policy(roots, writes, allow_ipc=False).text
    assert "ipc-posix-sem" not in closed and "(deny ipc*)" in closed
    assert closed.replace("(deny ipc*)\n", "") == default.replace(seatbelt.posix_sem_rule(), "")
    linux = bwrap.BACKEND.policy(roots, writes, allow_ipc=False).text
    assert linux == bwrap.BACKEND.policy(roots, writes).text
    assert {"--unshare-all", "--dev"} <= set(linux.splitlines())
