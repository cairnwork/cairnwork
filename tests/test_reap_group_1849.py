"""#1849: before reap() counts, it waits (capped) until the collector's process group holds nothing
but the collector itself, so the collector's own exiting children are not counted as escapes.
The count formula and the verdict stay as they were; processes outside the group, and any still
there at the cap, are counted as before; an unreadable group lookup means no wait (fail closed)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnwork.verifier_account as va
import cairnwork.verifier_group as vg
import cairnwork.verifier_seen as vs

MAIN = 100  # the collector's pid, which is also its group's id


class World:
    """A fake clock and a process table over it: rows are (pid, pgid, gone_at or None[, exempt])."""

    def __init__(self, rows):
        self.now, self.rows, self.sleeps, self.group_calls, self.rc = 0.0, rows, [], 0, 0

    def table(self):
        return [(r[0], r[1]) for r in self.rows if r[2] is None or self.now < r[2]]

    def counted(self):  # an exempt launchd daemon is in ps but not in the account's count
        return [r for r in self.rows if (r[2] is None or self.now < r[2]) and not r[3:]]

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def run(self, cmd, **kwargs):
        if "pid=,pgid=" not in cmd:  # the diagnostic's own ps (vs.before_kill): nothing to list
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        self.group_calls += 1
        if self.rc == "raise":
            raise subprocess.TimeoutExpired("ps", 1)
        text = "".join(f"{p} {g}\n" for p, g in self.table())
        return SimpleNamespace(returncode=self.rc, stdout=text, stderr="")


def _acct(monkeypatch, rows, **kw):
    world = World(rows)
    world.rc = kw.get("rc", 0)
    monkeypatch.setattr(vg.subprocess, "run", world.run)
    monkeypatch.setattr(vg, "_now", lambda: world.now)
    monkeypatch.setattr(vg, "_sleep", world.sleep)
    monkeypatch.setattr(vs.sys, "platform", "linux")
    monkeypatch.setattr(
        va.VerifierAccount, "_escaped_count", lambda self, d=None, attempts=1: len(world.counted())
    )
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda self, n, d: 0)
    return world, va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter"))


def test_a_child_in_mains_group_gone_at_half_a_second_is_waited_for(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 0.5)])
    assert acct.reap(1, group=MAIN) == 0
    assert 0.5 <= world.now <= vg.CAP_SECONDS + vg.POLL_SECONDS


def test_without_the_wait_the_same_child_is_counted(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 0.5)])
    assert acct.reap(1) == 1 and world.group_calls == 0 and world.sleeps == []


def test_a_child_outside_the_group_stays_counted_and_nothing_is_waited(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (200, 200, None)])
    assert acct.reap(1, group=MAIN) == 1 and world.sleeps == []


def test_a_child_past_the_cap_is_still_counted_within_cap_plus_one_poll(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 99.0)])
    assert acct.reap(1, group=MAIN) == 1
    assert vg.CAP_SECONDS <= world.now <= vg.CAP_SECONDS + vg.POLL_SECONDS


@pytest.mark.parametrize("rc", [2, "raise"])
def test_a_failed_group_lookup_means_no_wait_and_the_count_of_today(monkeypatch, rc):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 0.5)], rc=rc)
    assert acct.reap(1, group=MAIN) == 1 and world.sleeps == [] and world.group_calls == 1


def test_a_lookup_that_fails_while_waiting_ends_the_wait(monkeypatch):
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 99.0)])
    real = world.run

    def flaky(cmd, **kwargs):
        if world.group_calls >= 1:
            world.rc = 2
        return real(cmd, **kwargs)

    monkeypatch.setattr(vg.subprocess, "run", flaky)
    assert acct.reap(1, group=MAIN) == 1 and len(world.sleeps) == 1


def test_one_bounded_ps_per_poll(monkeypatch):
    seen = []
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 0.35)])
    real = world.run

    def spy(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(cmd, **kwargs)

    monkeypatch.setattr(vg.subprocess, "run", spy)
    acct.reap(1, group=MAIN)
    group_polls = world.group_calls
    assert group_polls == len(world.sleeps) + 1
    assert all(t is not None and 0 < t <= vg.PS_TIMEOUT_SECONDS for t in seen)


def test_the_wait_shares_the_probe_deadline(monkeypatch):
    monkeypatch.setattr(va, "PROBE_DEADLINE_SECONDS", 0.0)
    world, acct = _acct(monkeypatch, [(MAIN, MAIN, None), (101, MAIN, 99.0)])
    acct.reap(1, group=MAIN)
    assert world.sleeps == []


@pytest.mark.parametrize("main", [0, 1, 2])
@pytest.mark.parametrize("rows", [0, 1, 3])
def test_count_and_verdict_equal_mains_formula_when_the_group_is_clear(monkeypatch, main, rows):
    table = [(300 + n, 300 + n, None) for n in range(rows)]  # all outside the group
    _, plain = _acct(monkeypatch, table)
    want = plain.reap(main)
    _, grouped = _acct(monkeypatch, table)
    got = grouped.reap(main, group=MAIN)
    assert got == want and got.reasons == want.reasons and got.argv == want.argv


def test_an_unknown_count_and_a_left_over_kill_stay_as_they_were(monkeypatch):
    _, acct = _acct(monkeypatch, [(101, MAIN, None)])
    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", lambda self, d=None, attempts=1: -1)
    assert acct.reap(0, group=MAIN) == 1
    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", lambda self, d=None, attempts=1: 2)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda self, n, d: 1)
    assert acct.reap(0, group=MAIN) == 3


OUT = 200  # a process outside the collector's group
EX = (900, 900, None, "exempt")


def _count(monkeypatch, rows, main=0, group=MAIN):
    _, acct = _acct(monkeypatch, rows)
    return int(acct.reap(main, group=group))


@pytest.mark.parametrize(
    "rows,main",
    [
        ([(MAIN, MAIN, None), (OUT, OUT, 1.0), (101, MAIN, 1.5)], 1),  # 1 outsider gone first
        ([(MAIN, MAIN, None), (OUT, OUT, 1.9), (101, MAIN, 0.3)], 1),  # 2 outsider gone last
        ([(OUT, OUT, 1.0), (101, MAIN, 1.5)], 0),  # 3 outsider born of a dead group child
        ([(OUT, OUT, None)], 0),  # 4 outsider to the cap, group clear
        ([(101, MAIN, 2.01)], 0),  # 5 group child still there at the cap
        ([(101, MAIN, 0.3), (102, MAIN, None)], 0),  # 6 grandchild outlives the cap
        ([(101, 101, None)], 0),  # 7 setsid, lives on
    ],
)
def test_must_block_a_known_outsider_or_a_leftover_is_counted(monkeypatch, rows, main):
    assert _count(monkeypatch, rows, main) >= 1


def test_must_block_an_outsider_that_vanishes_during_the_wait_is_still_counted(monkeypatch):
    rows = [(MAIN, MAIN, None), (OUT, OUT, 1.0), (101, MAIN, 1.5)]
    main_got = _count(monkeypatch, [(MAIN, MAIN, None), (OUT, OUT, 0.0001 * 0 + 99.0)], 1)
    assert _count(monkeypatch, rows, 1) >= 1 and main_got >= 1


def test_must_block_no_wait_when_the_deadline_is_used_up(monkeypatch):
    monkeypatch.setattr(va, "PROBE_DEADLINE_SECONDS", 0.0)
    world, acct = _acct(monkeypatch, [(OUT, OUT, 1.0), (101, MAIN, 1.5)])
    assert world.sleeps == [] and int(acct.reap(0, group=MAIN)) >= 1


@pytest.mark.parametrize(
    "rows,main",
    [
        ([(MAIN, MAIN, None), (101, MAIN, 0.5)], 1),  # a: group child leaves, nothing outside
        ([(101, MAIN, 0.5)], 0),  # a without the collector
        ([(MAIN, MAIN, None), (101, MAIN, 0.4), (102, MAIN, 0.2)], 1),  # b: collector still runs
        ([(MAIN, MAIN, None), (101, MAIN, 0.5), EX], 1),  # c: exempt daemon stays exempt
    ],
)
def test_must_pass_the_collectors_own_children_are_not_counted(monkeypatch, rows, main):
    assert _count(monkeypatch, rows, main) == 0


def test_the_cap_is_two_seconds():
    assert vg.CAP_SECONDS == 2.0


@pytest.mark.parametrize("stderr", ["ps: no", " \nx"])
def test_a_lookup_with_error_output_is_unreadable_even_when_it_exits_zero(monkeypatch, stderr):
    done = SimpleNamespace(returncode=0, stdout=f"{MAIN} {MAIN}\n", stderr=stderr)
    monkeypatch.setattr(vg.subprocess, "run", lambda *a, **k: done)
    assert vg._members("x", MAIN, 1.0) is None


def test_reap_gets_the_collectors_group_from_execute_and_the_flag_is_on(monkeypatch, tmp_path):
    import cairnwork.confined as confined
    import cairnwork.verifier_exec as ve

    seen, pids = {}, []
    real = confined.subprocess.Popen

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            pids.append(self.pid)

    def reap(main=0, **kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(confined.subprocess, "Popen", Spy)
    account = SimpleNamespace(wrap=lambda argv, profile, env: argv, reap=reap)
    (tmp_path / "p").write_text("")
    env = {confined.RUN_TOKEN_VAR: "t", "PATH": "/usr/bin:/bin"}
    ve._execute(
        ["/usr/bin/true"], tmp_path / "p", str(tmp_path), env, 20, tmp_path / "log", account, None
    )
    assert seen == {"group": pids[0]}
