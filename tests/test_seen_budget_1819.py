"""#1819 diagnostics: the listing snapshot as a whole (ps, path lookup, signature checks) has one
bounded budget, and the deadline the kill works against moves by at most that budget."""

import time

from test_account_escaped_listing_1819 import EXEMPT, _acct_rows, _fake_run, _names

import cairnwork.verifier_escape as ve
import cairnwork.verifier_seen as vs


def _slow(seconds):
    def lookup(*args, **kwargs):
        time.sleep(seconds)
        return {int(p): (1.0, EXEMPT) for p in args[-1]}

    return lookup


def test_a_slow_path_lookup_ends_at_the_budget_and_says_unknown(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.4)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    monkeypatch.setattr(vs.subprocess, "run", _fake_run("10 1 S 00:09 d\n20 1 S 00:01 python\n"))
    start = time.monotonic()
    argv, reasons = vs.snapshot("x", 120.0, _slow(5.0))
    assert time.monotonic() - start < 2.0
    assert vs.UNKNOWN in reasons and len(argv) == 2  # nothing is hidden, nothing is guessed


def test_slow_signature_checks_add_up_to_no_more_than_the_budget(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.4)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    rows = "".join(f"{10 + i} 1 S 00:09 d{i}\n" for i in range(40))
    monkeypatch.setattr(vs.subprocess, "run", _fake_run(rows))

    def slow_check(path, ppid):
        time.sleep(0.2)
        return True

    monkeypatch.setattr(ve, "is_exempt", slow_check)
    fast = lambda pids: {p: (1.0, EXEMPT) for p in pids}  # noqa: E731
    start = time.monotonic()
    _, reasons = vs.snapshot("x", 120.0, fast)
    assert time.monotonic() - start < 2.0 and vs.UNKNOWN in reasons


def test_before_kill_never_moves_the_deadline_by_more_than_the_budget(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(vs.time, "monotonic", lambda: clock["now"])

    def slow_snapshot(*args):
        clock["now"] += 1000.0  # however long the diagnostic took
        return ((), ())

    monkeypatch.setattr(vs, "snapshot", slow_snapshot)
    _, moved = vs.before_kill("x", 1, 220.0)
    assert moved - 220.0 <= vs.BUDGET


def test_a_slow_lookup_through_reap_does_not_change_the_count(monkeypatch):
    monkeypatch.setattr(vs, "BUDGET", 0.3)
    got = _acct_rows(monkeypatch, 1, 0, "10 1 S 00:09 d\n20 15 S 00:01 python\n", paths=_slow(5.0))
    assert got == 1 and "python" in _names(got) and vs.UNKNOWN in got.reasons
