"""#436: the false-positive side of recovery's sweep by working directory (``kill_withheld_in``),
pinned with fakes only. docs/SECURITY.md "Recovery's sweep by working directory" names it with
the two residuals that have no test (PID reuse, path-based directory checks).

No real process is read or signalled. The autouse fixture replaces ``os.kill`` first, with a
recorder that never delegates and refuses any pid below 4,000,000, then every reader that would
reach the kernel (``_system``, ``_argmax``, ``_sysctl``, ``_platform_binary``, ``_started``,
``cwd_of``); ``_system`` raises, so a reader left unfaked fails the test instead of reading a real
process. Rule for this module (10/29): no live probe or mutant of proc_cwd on a real host."""

import os
import signal
from dataclasses import dataclass, field

import pytest
from test_proc_cwd import _procargs

from cairnwork import proc_cwd

ATTEMPT = "a0123456789abcdef"
FAKE = 4_000_001  # the only pid in the fake table; above any macOS pid
LOWEST_FAKE = 4_000_000


@dataclass
class World:
    """What the fake readers answer for ``FAKE``, and the signals the recorder saw."""

    env: list[bytes] = field(default_factory=lambda: [b"A=1"])
    platform: bool | None = True
    cwd: str | None = None
    signals: list[tuple[int, int]] = field(default_factory=list)

    def kill(self, pid, sig):
        if pid < LOWEST_FAKE:
            raise AssertionError(f"a real pid reached os.kill: {pid}")
        self.signals.append((pid, sig))


@pytest.fixture(autouse=True)
def world(monkeypatch):
    found = World()
    monkeypatch.setattr(proc_cwd.os, "kill", found.kill)  # first: before any reader is faked

    def no_system():
        raise AssertionError("a real process reader was called")

    def sysctl(names, size):
        assert names[-1] == FAKE and size == 4096
        return _procargs([b"x"], found.env)

    monkeypatch.setattr(proc_cwd, "_system", no_system)
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_argmax", lambda: 4096)
    monkeypatch.setattr(proc_cwd, "_sysctl", sysctl)
    monkeypatch.setattr(proc_cwd, "_platform_binary", lambda pid: found.platform)
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"same start")  # pid not reused
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: found.cwd)
    return found


@pytest.fixture
def runs(tmp_path):
    root = tmp_path / "runs"
    (root / ATTEMPT / "sub").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    return root


def _sweep(world, runs):
    assert proc_cwd.os.kill == world.kill  # the recorder is in place before the sweep runs
    dirs = proc_cwd.attempt_dirs([runs], ATTEMPT)
    assert dirs == ((runs / ATTEMPT).resolve(),)
    rows = [(FAKE, FAKE, os.getuid(), {"x"})]
    return proc_cwd.kill_withheld_in(rows, dirs, os.getuid())


def test_the_recorder_refuses_a_real_pid(world):
    assert proc_cwd.os.kill == world.kill
    with pytest.raises(AssertionError, match="a real pid"):
        world.kill(os.getpid(), 0)  # called directly: nothing is sent either way
    assert world.signals == []


def test_a_platform_binary_with_one_real_entry_in_a_run_dir_is_killed(world, runs):
    """The documented residual (SECURITY.md, #436 item 1): on a macOS that shows platform
    binaries' environments, one showing exactly one real entry looks like the withheld case an
    empty argv[0] produces, so it is tied by its directory alone and killed."""
    world.cwd = str(runs / ATTEMPT / "sub")  # unresolved: tmp is a symlink on macOS
    assert _sweep(world, runs) == ([FAKE], [], 0)
    assert world.signals == [(FAKE, signal.SIGKILL)]


@pytest.mark.parametrize(
    ("env", "platform", "where"),
    [
        ([b"A=1"], True, "elsewhere"),  # bounded to run directories
        ([b"A=1"], False, ATTEMPT),  # an ordinary program's one entry is real
        ([b"A=1"], None, ATTEMPT),  # code-signing status unreadable: unknown, ignored
        ([b"A=1", b"B=2"], True, ATTEMPT),  # two entries are never the empty-argv[0] case
    ],
    ids=["outside-run-dirs", "not-platform", "status-unknown", "two-entries"],
)
def test_the_false_positive_goes_no_further(world, runs, env, platform, where):
    world.env, world.platform = env, platform
    world.cwd = str(runs / ATTEMPT / "sub") if where == ATTEMPT else str(runs.parent / where)
    assert _sweep(world, runs) == ([], [], 0)
    assert world.signals == []
