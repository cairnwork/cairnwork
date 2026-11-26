"""#1819 diagnostics, structure (layer1 ruling after two cross-family rejections): before the kill
only ONE raw, bounded ps read; everything else (parse, exempt filtering, sort, main drop, entries)
runs after the kill, inline, within its own budget, and can never touch the kill's deadline, the
count or the verdict."""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnwork.confined as confined
import cairnwork.verifier_account as va
import cairnwork.verifier_escape as ve
import cairnwork.verifier_seen as vs

EXEMPT = "/usr/libexec/exempt"
ROWS = "10 1 S 00:09 d\n20 1 S 00:02 python\n21 1 S 00:02 sleep\n30 99 S 00:01 bash\n"


class Env:
    """reap() for real, with ps, _escaped_count and _kill_until_clean faked on a fake clock.
    ``slow`` is how many fake seconds each post-kill step (path lookup, signature check, entry,
    parse of a row) takes; ``only`` names the one step that is slow (default: all)."""

    def __init__(self, monkeypatch, before, rows=ROWS, left=0, ps_cost=2.0, slow=0.0, only=None):
        self.log, self.deadlines, self.ps_elapsed, self.ps_timeouts = [], [], [], []
        self.clock = {"now": 1000.0}
        mp = monkeypatch

        def cost(step):
            return slow if only in (None, step) else 0.0

        mp.setattr(vs.time, "monotonic", lambda: self.clock["now"])
        mp.setattr(vs.sys, "platform", "darwin")

        def run(cmd, **kwargs):
            self.log.append("ps")
            self.ps_timeouts.append(kwargs["timeout"])
            spent = min(ps_cost, kwargs["timeout"])
            self.ps_elapsed.append(spent)
            self.clock["now"] += spent
            return SimpleNamespace(returncode=0, stdout=rows, stderr="")

        def kill(acct, attempts, deadline):
            self.log.append("kill")
            self.deadlines.append(deadline)
            return left

        def paths(acct, pids, deadline=None):
            self.log.append("real_paths")
            self.clock["now"] += cost("real_paths")
            return {p: (1.0, EXEMPT) for p in pids}

        def exempt(path, ppid):
            self.log.append("is_exempt")
            self.clock["now"] += cost("is_exempt")
            return path == EXEMPT

        real_entry, real_plain = vs._entry, vs._plain_ppid

        def entry(*args):
            self.log.append("entry")
            self.clock["now"] += cost("entry")
            return real_entry(*args)

        def plain(text):
            self.log.append("parse")
            self.clock["now"] += cost("parse")
            return real_plain(text)

        mp.setattr(vs.subprocess, "run", run)
        mp.setattr(va.VerifierAccount, "_escaped_count", lambda s, d=None, attempts=1: before)
        mp.setattr(va.VerifierAccount, "_kill_until_clean", kill)
        mp.setattr(va.VerifierAccount, "_real_paths", paths)
        mp.setattr(ve, "is_exempt", exempt)
        mp.setattr(vs, "_entry", entry)
        mp.setattr(vs, "_plain_ppid", plain)
        self.acct = va.VerifierAccount(user="x", root=Path("/tmp/none"))

    def reap(self, main=0):
        return self.acct.reap(main)


def _formula(before, main, left):
    return max(before, 0) + 1 if before < 0 or left != 0 else max(before - main, 0)


@pytest.mark.parametrize("slow", [0.0, 5.0, 1000.0])
def test_the_kill_deadline_moves_only_by_the_one_ps_read(monkeypatch, slow):
    env = Env(monkeypatch, 4, slow=slow, ps_cost=2.0)
    env.reap(1)
    assert env.deadlines == [1000.0 + va.PROBE_DEADLINE_SECONDS + 2.0]


def test_a_very_slow_ps_moves_the_deadline_by_its_timeout_at_most(monkeypatch):
    env = Env(monkeypatch, 4, ps_cost=500.0, slow=1000.0)
    env.reap(1)
    moved = env.deadlines[0] - (1000.0 + va.PROBE_DEADLINE_SECONDS)
    assert moved == env.ps_elapsed[0] <= env.ps_timeouts[0] <= confined.PS_TIMEOUT_SECONDS


def test_the_pre_kill_phase_is_one_ps_and_parses_nothing(monkeypatch):
    env = Env(monkeypatch, 4)
    env.reap(1)
    first_kill = env.log.index("kill")
    assert env.log[:first_kill] == ["ps"]
    after = env.log[first_kill + 1 :]
    assert {"real_paths", "is_exempt", "entry", "parse"} <= set(after)


def test_nothing_is_read_when_nothing_is_running(monkeypatch):
    env = Env(monkeypatch, 0)
    assert env.reap() == 0 and env.log == ["kill"]


@pytest.mark.parametrize(
    ("before", "main", "left"),
    [(4, 1, 0), (4, 0, 0), (3, 5, 0), (0, 0, 0), (-1, 0, 0), (2, 0, 1), (3, 1, -1), (4, 1, 1)],
)
@pytest.mark.parametrize("slow", [0.0, 1000.0])
def test_count_and_verdict_equal_main_however_slow_the_listing(
    monkeypatch, before, main, left, slow
):
    got = Env(monkeypatch, before, left=left, slow=slow).reap(main)
    assert got == _formula(before, main, left)


@pytest.mark.parametrize("what", ["real_paths", "is_exempt", "entry", "parse"])
def test_a_slow_post_kill_phase_says_incomplete_and_unknown_never_raises(monkeypatch, what):
    env = Env(monkeypatch, 4, slow=1000.0, only=what)
    got = env.reap(1)
    assert got == 3 and vs.INCOMPLETE in got.reasons and vs.UNKNOWN in got.reasons
    assert got.reasons[-1] == "main=1"
    assert what in env.log  # the step ran, after the kill


def test_a_failing_step_after_the_kill_gives_the_plain_count(monkeypatch):
    env = Env(monkeypatch, 4)

    def boom(*args):
        raise RuntimeError("diagnostic bug")

    monkeypatch.setattr(vs, "_entry", boom)
    got = env.reap(1)
    assert got == 3 and got.argv == () and got.reasons == ()


def test_no_worker_thread_is_left_after_a_timeout(monkeypatch):
    # real clock: a real slow lookup that outlives the budget
    monkeypatch.setattr(vs, "BUDGET", 0.2)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    monkeypatch.setattr(
        vs.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=ROWS, stderr="")
    )
    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", lambda s, d=None, attempts=1: 4)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda s, n, d: 0)

    def slow(acct, pids, deadline=None):
        time.sleep(0.5)
        return {p: (1.0, EXEMPT) for p in pids}

    monkeypatch.setattr(va.VerifierAccount, "_real_paths", slow)
    threads = threading.active_count()
    got = va.VerifierAccount(user="x", root=Path("/tmp/none")).reap(1)
    assert threading.active_count() == threads and not hasattr(vs, "threading")
    assert got == 3 and vs.UNKNOWN in got.reasons
