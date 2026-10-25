"""Codex #244 round 2: a scan in which a marked process vanished is not trusted. A listed
worker that forks and exits before its kill may leave a child the listing missed, so a look
with a vanished marked process is not gone, and confirm_gone needs two clean looks in a row,
taken after every kill. The process table and the kill are faked."""

import pytest
from test_sweep_result import FAKE, _marked, _table

import cairnwork.confined as confined
from cairnwork.confined import confirm_gone, sweep_marked


def _vanished(_pid):
    raise ProcessLookupError


def test_a_marked_process_that_exits_before_its_kill_taints_the_look(monkeypatch):
    """Codex #244 round 2: a listed worker that forks and exits before the kill may leave a
    child this look missed; ``vanished`` keeps the look from being gone."""
    _table(monkeypatch, [_marked()], kill=_vanished)
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1")
    assert (found.vanished, found.found, found.gone) == (1, 0, False)
    assert found.problem().startswith("SURVIVORS_REMAIN") and found.held_by == ()


@pytest.mark.parametrize(
    "looks",
    [
        [[_marked(FAKE)], [_marked(FAKE + 1)], []],  # the child shows on the next look
        [[_marked(FAKE)], [], [_marked(FAKE + 1)], []],  # one clean look is not enough
    ],
)
def test_a_child_left_by_a_parent_that_exited_before_its_kill_is_swept(monkeypatch, looks):
    """Codex #244 round 2: the parent is listed, forks and exits before its kill; the listing
    missed the child. Gone needs two clean looks in a row, taken after every kill."""

    def kill(pid):
        if pid == FAKE:
            raise ProcessLookupError  # the parent: already exited

    killed = _table(monkeypatch, *looks, kill=kill)

    def look():
        return sweep_marked("CAIRNWORK_ATTEMPT", "a1")

    first = look()
    assert not first.gone  # it used to be gone here, and the takeover went ahead
    last = confirm_gone(first, look)
    assert last.gone and killed == [FAKE, FAKE + 1]


def test_one_clean_look_at_the_end_of_the_window_keeps_the_fence(monkeypatch):
    _table(monkeypatch, *([[_marked()]] * confined.CONFIRM_TRIES), [])

    def look():
        return sweep_marked("CAIRNWORK_ATTEMPT", "a1")

    last = confirm_gone(look(), look)
    assert last.listed and last.unconfirmed and not last.gone
    assert last.problem().startswith("SURVIVORS_REMAIN")


def test_a_real_parent_that_forks_and_exits_before_the_kill(monkeypatch, tmp_path):
    """Real processes: a marked parent starts a child and exits. The first listing is the one
    taken before the fork (only the parent); the kill finds the parent gone; the child must
    still be found and killed before the attempt counts as gone."""
    import os
    import secrets
    import signal
    import subprocess
    import sys

    value = "a" + secrets.token_hex(8)
    child_pid_file = str(tmp_path / "child.pid")
    code = (
        "import os, subprocess, sys; "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"open({child_pid_file!r}, 'w').write(str(c.pid))"
    )
    parent = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "CW_V": value})
    parent.wait(timeout=30)  # exited and reaped: a kill now finds no such process
    child = int(open(child_pid_file).read())
    real, real_kill, signalled = confined._processes_with_environment, os.kill, []

    def kill(pid, sig):
        signalled.append(pid)
        return real_kill(pid, sig)

    monkeypatch.setattr(confined.os, "kill", kill)
    looks = []

    def processes():
        looks.append(1)
        if len(looks) == 1:  # the listing taken before the fork: the parent only
            return [(parent.pid, parent.pid, os.getuid(), {f"CW_V={value}"})]
        return real()

    monkeypatch.setattr(confined, "_processes_with_environment", processes)
    try:
        first = sweep_marked("CW_V", value)
        assert first.vanished == 1 and not first.gone
        last = confirm_gone(first, lambda: sweep_marked("CW_V", value))
        # not .gone: a busy host may hide another of the owner's environments (as in
        # test_sweep_result's real test); the attempt's own processes are what count here
        assert last.listed and last.found == 0 and last.vanished == 0, last.problem()
        assert signalled[0] == parent.pid and child in signalled  # found on a later look
    finally:
        try:
            real_kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_the_two_clean_looks_must_be_in_a_row():
    """A dirty look between two clean ones starts the count again."""
    from cairnwork.confined import Sweep

    answers, calls = [Sweep(True, killed=1), Sweep(True), Sweep(True)], []

    def again():
        calls.append(1)
        return answers[min(len(calls), len(answers)) - 1]

    assert confirm_gone(Sweep(True), again).gone
    assert len(calls) == 3  # clean, dirty, clean, clean: not done after the second clean one


def test_an_owner_that_cannot_be_read_counts_as_the_owners_in_both_sweeps(monkeypatch):
    """Claude review of 51cff7d: an unreadable owner must keep the fence with and without a
    worker account (it is judged as one of the owner's: a hidden environment is unreadable)."""
    from test_sweep_result import OTHER_UID

    _table(monkeypatch, [(FAKE, None, confined.os.getuid(), None)])
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID)
    for account in (None, "w"):
        found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account=account)
        assert found.unreadable == 1 and not found.gone
        assert found.kept_by() == f"{FAKE} hidden"
