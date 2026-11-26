"""#1861 round 4: a test's own detached processes stay killable, strangers stay refused, and the
start times the guard compares do not depend on the time zone. Fake ps tables and recorders only:
nothing is ever signalled."""

import calendar
import os
import time

import adopt_helpers
import kill_guard
import pytest
from kill_guard import KillGuardError
from test_kill_guard_failclosed import _fake_ps, _guard

ME = os.getpid()
ROOT, KID, STRANGER = ME + 50001, ME + 50002, ME + 50003
NEW = 4_000_000_000  # started after the test began
OLD = 1_000  # started long before it


def _setup(monkeypatch, rows, starts=None):
    """The guard installed as os.kill, the real kill (also kill_guard.REAL_KILL) a recorder."""
    guard, sent = _guard()
    _fake_ps(monkeypatch, {ME: (1, ME)} | rows, {ME: OLD} | (starts or {}))
    monkeypatch.setattr(kill_guard, "ACTIVE", [guard])
    monkeypatch.setattr(kill_guard, "REAL_KILL", lambda p, s: sent.append(("real", p, s)))
    monkeypatch.setattr(os, "kill", guard.kill)
    return sent


def _pid_file(tmp_path, text):
    path = tmp_path / "own.pid"
    path.write_text(text)
    return path


def test_a_pid_file_names_a_detached_process_and_what_runs_below_it(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, {ROOT: (1, ROOT), KID: (ROOT, KID), STRANGER: (1, STRANGER)})
    adopt_helpers.adopt_on_kill(monkeypatch, _pid_file(tmp_path, str(ROOT)))
    os.kill(ROOT, 9)
    os.kill(KID, 9)
    assert sent == [("real", ROOT, 9), ("real", KID, 9)]
    with pytest.raises(KillGuardError):
        os.kill(STRANGER, 9)
    assert len(sent) == 2


@pytest.mark.parametrize("text", ["", "not a pid", None])  # empty, garbage, no file
def test_an_unreadable_pid_file_names_nobody(monkeypatch, tmp_path, text):
    sent = _setup(monkeypatch, {ROOT: (1, ROOT)})
    path = tmp_path / "none.pid" if text is None else _pid_file(tmp_path, text)
    adopt_helpers.adopt_on_kill(monkeypatch, path)
    with pytest.raises(KillGuardError):
        os.kill(ROOT, 9)
    assert sent == []


def test_a_number_the_file_names_that_is_gone_does_not_cover_a_stranger_below_it(
    monkeypatch, tmp_path
):
    sent = _setup(monkeypatch, {STRANGER: (ROOT, STRANGER)})  # ROOT left the table: reused
    adopt_helpers.adopt_on_kill(monkeypatch, _pid_file(tmp_path, str(ROOT)))
    with pytest.raises(KillGuardError):
        os.kill(STRANGER, 9)
    assert sent == []


def test_signal_zero_is_never_redirected(monkeypatch, tmp_path):
    sent = _setup(monkeypatch, {ROOT: (1, ROOT)})
    adopt_helpers.adopt_on_kill(monkeypatch, _pid_file(tmp_path, str(ROOT)))
    os.kill(ROOT, 0)
    assert sent == [("kill", ROOT, 0)]  # the guard's own pass-through, not REAL_KILL


@pytest.mark.parametrize("start,passes", [(NEW, True), (OLD, False), (None, False)])
def test_a_process_nobody_can_name_is_taken_only_if_it_started_during_the_test(
    monkeypatch, start, passes
):
    starts = {} if start is None else {ROOT: start}
    sent = _setup(monkeypatch, {ROOT: (1, ROOT)}, starts)
    adopt_helpers.adopt_started_in_test(monkeypatch)
    if passes:
        os.kill(ROOT, 9)
        assert sent == [("kill", ROOT, 9)]
    else:
        with pytest.raises(KillGuardError):
            os.kill(ROOT, 9)
        assert sent == []


class _Ps:
    returncode = 0
    seen: list = []

    def __init__(self, argv, **kwargs):
        self.seen.append(kwargs.get("env"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, timeout=None):
        return "  4242 Thu Nov 26 18:18:21 2026\n", ""


@pytest.mark.parametrize("zone", ["UTC0", "Pacific/Honolulu", "Pacific/Kiritimati"])
def test_start_times_do_not_depend_on_the_time_zone(monkeypatch, zone):
    monkeypatch.setattr(kill_guard, "_REAL_POPEN", _Ps)
    _Ps.seen = []
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    try:
        got = kill_guard._starts()
    finally:
        monkeypatch.undo()
        time.tzset()
    assert got == {4242: calendar.timegm((2026, 11, 26, 18, 18, 21))}
    assert _Ps.seen[0]["TZ"] == "UTC0"  # ps prints lstart in UTC, whatever the host zone is
