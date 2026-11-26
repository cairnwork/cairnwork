"""Kill guard fails closed (#1861 review): unreadable table, reaped children, broad excepts.

Every real kill is a recorder: nothing is ever signalled.
"""

import os
import subprocess
import sys

import kill_guard
import pytest
from kill_guard import Guard, KillGuardError

FOREIGN = os.getpid() + 40001


def _guard():
    sent = []
    return Guard(
        lambda p, s: sent.append(("kill", p, s)), lambda g, s: sent.append(("pg", g, s))
    ), sent


def _fake_ps(monkeypatch, rows, starts=None):
    text = "".join(f"{p} {pp} {pg}\n" for p, (pp, pg) in rows.items())
    monkeypatch.setattr(kill_guard, "_ps", lambda: (0, text))
    given = dict(starts or {})
    monkeypatch.setattr(kill_guard, "_starts", lambda: given, raising=False)


@pytest.mark.parametrize("table", [{}, {FOREIGN: (1, FOREIGN)}])
def test_a_table_without_this_process_blocks_everything(monkeypatch, table):
    _fake_ps(monkeypatch, table)
    guard, sent = _guard()
    with pytest.raises(KillGuardError):
        guard.kill(FOREIGN, 9)
    with pytest.raises(KillGuardError):
        guard.killpg(FOREIGN, 9)
    assert sent == []


@pytest.mark.parametrize("ps", [(1, ""), (0, "")])
def test_a_failed_or_empty_ps_raises(monkeypatch, ps):
    monkeypatch.setattr(kill_guard, "_ps", lambda: ps)
    with pytest.raises(KillGuardError):
        kill_guard._table()


class _HungPs:
    returncode = None

    def __init__(self, *a, **k):
        self.killed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def communicate(self, timeout=None):
        if self.killed:
            return "", ""
        assert timeout is not None  # _ps always passes one
        raise subprocess.TimeoutExpired("ps", timeout)

    def kill(self):
        self.killed = True


def test_a_hung_ps_raises(monkeypatch):
    monkeypatch.setattr(kill_guard, "_REAL_POPEN", _HungPs)
    with pytest.raises(KillGuardError):
        kill_guard._table()


def test_a_reaped_child_number_reused_by_a_stranger_is_refused(monkeypatch):
    guard, sent = _guard()
    child = subprocess.Popen.__new__(subprocess.Popen)
    guard.record(subprocess.Popen.__init__)(child, [sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    me = os.getpid()
    table = {me: (1, me), child.pid: (1, child.pid)}  # the number now names a stranger
    _fake_ps(monkeypatch, table)
    with pytest.raises(KillGuardError):
        guard.kill(child.pid, 9)
    with pytest.raises(KillGuardError):
        guard.killpg(child.pid, 9)
    assert sent == []


def test_the_guard_error_passes_through_except_exception():
    assert not issubclass(KillGuardError, Exception)


# --- #1861 round 3: a recycled pid or group number must not pass as lineage / adopted ---
ME = os.getpid()
F = ME + 40002
OLD, NEW = 1_000_000_000, 1_900_000_000  # fake process start times (epoch seconds)


def test_a_lineage_pid_recycled_by_a_stranger_is_refused(monkeypatch):
    guard, sent = _guard()
    _fake_ps(monkeypatch, {ME: (1, ME), F: (ME, F)}, {ME: OLD, F: NEW})
    guard._look()  # F is below us now and is remembered
    _fake_ps(monkeypatch, {ME: (1, ME), F: (1, 777)}, {ME: OLD, F: NEW + 500})
    with pytest.raises(KillGuardError):
        guard.kill(F, 9)
    assert sent == []


def test_a_lineage_group_recycled_by_strangers_is_refused(monkeypatch):
    guard, sent = _guard()
    _fake_ps(
        monkeypatch,
        {ME: (1, ME), 777: (ME, 777), 778: (777, 777)},
        {ME: OLD, 777: NEW, 778: NEW},
    )
    guard._look()
    _fake_ps(
        monkeypatch,
        {ME: (1, ME), 778: (1, 777), F: (1, 777)},
        {ME: OLD, 778: NEW + 500, F: NEW + 500},
    )
    with pytest.raises(KillGuardError):
        guard.killpg(777, 9)
    assert sent == []


def test_a_genuine_orphan_of_ours_still_passes(monkeypatch):
    guard, sent = _guard()
    _fake_ps(monkeypatch, {ME: (1, ME), F: (ME, F)}, {ME: OLD, F: NEW})
    guard._look()
    _fake_ps(monkeypatch, {ME: (1, ME), F: (1, F)}, {ME: OLD, F: NEW})  # reparented, same start
    guard.kill(F, 9)
    assert sent == [("kill", F, 9)]


@pytest.mark.parametrize("starts", [{ME: OLD}, {ME: OLD, F: 1_000}])  # unknown / before the test
def test_adopting_a_process_not_started_after_the_test_began_is_refused(monkeypatch, starts):
    guard, sent = _guard()
    _fake_ps(monkeypatch, {ME: (1, ME), F: (1, F)}, starts)
    monkeypatch.setattr(kill_guard, "ACTIVE", [guard])
    kill_guard.adopt(F)
    with pytest.raises(KillGuardError):
        guard.kill(F, 9)
    assert sent == []


def test_adopting_a_process_started_after_the_test_began_passes(monkeypatch):
    guard, sent = _guard()
    _fake_ps(monkeypatch, {ME: (1, ME), F: (1, F)}, {ME: OLD, F: 4_000_000_000})
    monkeypatch.setattr(kill_guard, "ACTIVE", [guard])
    kill_guard.adopt(F)
    guard.kill(F, 9)
    assert sent == [("kill", F, 9)]
