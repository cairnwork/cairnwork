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


def _fake_ps(monkeypatch, rows):
    text = "".join(f"{p} {pp} {pg}\n" for p, (pp, pg) in rows.items())
    monkeypatch.setattr(kill_guard, "_ps", lambda: (0, text))


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
