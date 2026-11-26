"""#1819 diagnostics: the post-kill listing as a whole (parse, path lookup, signature checks,
entries) has one bounded budget, works inline (no thread) and the pre-kill ps read is the only
thing that moves the kill's deadline, by at most the ps timeout."""

import time

from test_account_escaped_listing_1819 import EXEMPT, _acct_rows, _names

import cairnwork.verifier_escape as ve
import cairnwork.verifier_seen as vs

ROWS = "10 1 S 00:09 d\n20 1 S 00:01 python\n"


def _slow(seconds):
    def lookup(*args, **kwargs):
        time.sleep(seconds)
        return {int(p): (1.0, EXEMPT) for p in args[-1]}

    return lookup


def _result(rows, paths, main=0):
    return vs.result(2, rows, paths, main, 2)


def test_a_slow_path_lookup_ends_at_the_budget_and_says_unknown(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.4)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    got = _result(ROWS, _slow(0.6))
    assert vs.UNKNOWN in got.reasons and vs.INCOMPLETE in got.reasons


def test_slow_signature_checks_stop_at_the_budget_between_checks(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.4)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    rows = "".join(f"{10 + i} 1 S 00:09 d{i}\n" for i in range(40))
    checked = []

    def slow_check(path, ppid):
        checked.append(path)
        time.sleep(0.2)
        return True

    monkeypatch.setattr(ve, "is_exempt", slow_check)
    fast = lambda pids: {p: (1.0, EXEMPT) for p in pids}  # noqa: E731
    start = time.monotonic()
    got = vs.result(40, rows, fast, 0, 40)
    assert time.monotonic() - start < 2.0 and vs.UNKNOWN in got.reasons
    assert len(checked) < 40  # it stopped, it did not check them all


def test_before_kill_never_moves_the_deadline_by_more_than_the_budget(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(vs.time, "monotonic", lambda: clock["now"])

    def slow_ps(*args, **kwargs):
        clock["now"] += 1000.0  # however long the read took
        raise vs.subprocess.TimeoutExpired("ps", 1)

    monkeypatch.setattr(vs.subprocess, "run", slow_ps)
    raw, moved = vs.before_kill("x", 1, 220.0)
    assert raw is None and moved - 220.0 <= vs.BUDGET


def test_nothing_is_read_before_the_kill_when_nothing_is_running():
    assert vs.before_kill("x", 0, 220.0) == (None, 220.0)


def test_a_slow_lookup_through_reap_does_not_change_the_count(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.3)
    got = _acct_rows(monkeypatch, 1, 0, "10 1 S 00:09 d\n20 15 S 00:01 python\n", paths=_slow(0.5))
    assert got == 1 and vs.UNKNOWN in got.reasons
    assert vs.INCOMPLETE in got.reasons  # nothing was cut to the count, and it says so
    assert _names(got) in ([], ["python"])  # the listing may be cut, never invented
