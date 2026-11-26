"""#1819 grace: a short-lived process (the collector's own exit, a one-line child) must not refuse
a harmless change, while anything still there when the grace ends is refused as before.

Seams: ``_escaped_count`` (counts in order), ``_kill_until_clean``, a fake clock on ``va.time``.
"""

import sys
import time
import uuid
from pathlib import Path
from typing import Any

import cairnwork.confined as confined
import cairnwork.verifier_account as va
import cairnwork.verifier_grace as vg
import cairnwork.verifier_seen as vs

LISTING = (("python R 00.01 ppid=9/-",), ("account-escaped",))


def _world(monkeypatch, counts, kill_left=0):
    """Counts are handed out in order (the last one repeats). Returns (acct, log, clock)."""
    log, clock = [], {"now": 0.0}
    seq = list(counts)

    def count(self, deadline=None, attempts=1):
        log.append("count")
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def kill(self, attempts, deadline):
        log.append("kill")
        return kill_left

    def sleep(seconds):
        log.append("sleep")
        clock["now"] += seconds

    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", count)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", kill)
    monkeypatch.setattr(vs, "snapshot", lambda user, timeout: LISTING)
    monkeypatch.setattr(va.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(va.time, "sleep", sleep)
    return va.VerifierAccount(user="x", root=Path("/tmp/none")), log, clock


def test_the_grace_is_a_named_cap_of_two_seconds_in_quarter_second_steps():
    assert vg.GRACE_SECONDS == 2.0 and vg.GRACE_INTERVAL == 0.25


def test_a_child_gone_inside_the_grace_counts_zero(monkeypatch):
    acct, _, _ = _world(monkeypatch, [2, 1])  # main + a short-lived child, then only main
    assert acct.reap(1) == 0


def test_a_process_alive_past_the_grace_is_refused_with_its_listing(monkeypatch):
    acct, _, clock = _world(monkeypatch, [2])
    got = acct.reap(1)
    assert got == 1 and got.argv == LISTING[0] and "account-escaped" in got.reasons
    assert clock["now"] <= vg.GRACE_SECONDS + 1e-9  # the cap is spent, not exceeded


def test_a_relay_of_changing_processes_is_refused(monkeypatch):
    acct, _, _ = _world(monkeypatch, [2, 3, 2, 3, 2, 3, 2, 3, 2, 3])  # never "nothing there"
    assert acct.reap(1) >= 1


def test_a_daemon_still_present_when_the_grace_ends_is_refused(monkeypatch):
    acct, _, _ = _world(monkeypatch, [4, 3, 2])  # detached daemon never leaves, main excluded
    assert acct.reap(1) >= 1


def test_the_default_path_takes_no_grace_and_no_extra_count(monkeypatch):
    acct, log, clock = _world(monkeypatch, [1])
    assert acct.reap(1) == 0
    assert log == ["count", "kill"] and clock["now"] == 0.0


def test_nothing_counted_takes_no_grace(monkeypatch):
    acct, log, _ = _world(monkeypatch, [0])
    assert acct.reap() == 0 and log == ["count", "kill"]


def test_an_unknown_count_is_refused_without_spending_the_grace(monkeypatch):
    acct, log, clock = _world(monkeypatch, [-1])
    assert acct.reap(1) == 1 and "sleep" not in log and clock["now"] == 0.0


def test_the_grace_is_spent_before_the_last_kill(monkeypatch):
    """Everything a short-lived process did is read after the last kill: the kill must come after
    the grace, so swapping the order turns this red."""
    acct, log, _ = _world(monkeypatch, [2, 1])
    acct.reap(1)
    assert log.index("kill") > max(i for i, e in enumerate(log) if e == "sleep")
    assert log[-1] == "kill"


def test_a_kill_that_leaves_something_still_refuses_after_a_clean_grace(monkeypatch):
    acct, _, _ = _world(monkeypatch, [2, 1], kill_left=1)
    assert acct.reap(1) >= 1


def test_main_gone_with_one_resident_left_is_refused_with_its_listing(monkeypatch):
    """pre-existing hole: ``main`` was a plain number. If the main run already exited and one
    resident process remains, count - main must not read as zero."""
    acct, _, _ = _world(monkeypatch, [1])
    got = acct.reap(vg.Main(1, lambda: False))
    assert got == 1 and got.argv == LISTING[0]


def test_main_still_alive_is_taken_off_as_before(monkeypatch):
    acct, _, _ = _world(monkeypatch, [1])
    assert acct.reap(vg.Main(1, lambda: True)) == 0


def test_a_main_that_dies_during_the_grace_stops_being_taken_off(monkeypatch):
    state = {"alive": True}
    acct, _, _ = _world(monkeypatch, [2, 1])  # the child went, main is the one left...
    ticking = va.time.sleep

    def sleep(seconds):
        state["alive"] = False
        ticking(seconds)

    monkeypatch.setattr(va.time, "sleep", sleep)
    assert acct.reap(vg.Main(1, lambda: state["alive"])) == 1  # ...but main is gone: a resident


def test_confined_hands_reap_a_main_whose_alive_follows_the_run(tmp_path):
    """The wiring in confined.execute: a stopped run that is still going is handed over as a
    ``Main`` of 1 whose ``alive`` is true, and it turns false once the run has really ended."""
    seen = []

    def reap(main: Any = 0):
        seen.append((isinstance(main, vg.Main), int(main), main.alive()))
        time.sleep(1.0)  # the child ends by itself
        seen.append(main.alive())
        return 0

    env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    confined.execute(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        tmp_path, env, 60, tmp_path / "log", reap=reap, stop=lambda: True,
    )  # fmt: skip
    assert seen == [(True, 1, True), False]


class _Slow:
    """An account whose every recount costs ``cost`` seconds of the fake clock."""

    def __init__(self, clock, cost, answers):
        self.clock, self.cost, self.answers, self.deadlines = clock, cost, list(answers), []

    def _escaped_count(self, deadline, attempts=1):
        self.deadlines.append(deadline)
        self.clock["now"] += self.cost
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _fake_clock(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(vg.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(vg.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    return clock


def test_a_slow_recount_cannot_stretch_the_wait_past_the_cap(monkeypatch):
    clock = _fake_clock(monkeypatch)
    account = _Slow(clock, cost=50.0, answers=[2])  # every recount eats the probe budget
    start = clock["now"]
    deadline = start + 120.0
    _, _, new_deadline = vg.settle(account, 2, 1, deadline)
    assert new_deadline - deadline <= vg.GRACE_SECONDS + 1e-9
    assert all(d <= start + vg.GRACE_SECONDS + 1e-9 for d in account.deadlines)


def test_a_slow_recount_that_finds_nothing_beyond_main_still_passes(monkeypatch):
    clock = _fake_clock(monkeypatch)
    account = _Slow(clock, cost=0.5, answers=[1])  # the child is gone on the first recount
    before, taken, _ = vg.settle(account, 2, vg.Main(1, lambda: True), clock["now"] + 120.0)
    assert (before, taken) == (1, 1)


def test_an_alive_probe_that_raises_is_a_survivor_not_a_crash(monkeypatch):
    clock = _fake_clock(monkeypatch)

    def boom():
        raise OSError("ps gone")

    account = _Slow(clock, cost=0.0, answers=[1])
    before, taken, _ = vg.settle(account, 1, vg.Main(1, boom), clock["now"] + 120.0)
    assert (before, taken) == (1, 0)  # main is not taken off: the one process is refused
