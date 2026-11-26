"""#1819 diagnostics: processes the account-level reap() counts are listed (bounded argv prefix,
state, age, fixed reason ``account-escaped``). Text only: counts and verdicts stay as before.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import cairnwork.survivor_argv as sa
import cairnwork.verifier_account as va
import cairnwork.verifier_seen as vs

PS = "  10     1 S      01:02 /usr/bin/python3.13\n  11    10 Z+     00:03 sleep\n"


def _fake_run(stdout, rc=0, stderr=""):
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    return run


def _snap(monkeypatch, stdout, **kw):
    monkeypatch.setattr(vs.subprocess, "run", _fake_run(stdout, **kw))
    return vs.snapshot("x", 5.0)


def test_rows_become_bounded_argv_prefixes_with_state_and_age(monkeypatch):
    argv, reasons = _snap(monkeypatch, PS)
    assert argv == ("sleep Z+ 00.03", "python3.13 S 01.02")  # launchd child last
    assert reasons == ("account-escaped",)


def test_a_candidate_chosen_name_is_hidden_not_echoed(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 S 00:01 /tmp/x/##[error]evil\n")
    assert argv == ("<redacted> S 00.01",)


def test_no_listed_line_carries_control_characters_or_workflow_prefixes(monkeypatch):
    rows = "5 1 S 00:01 python\x1b[31m ::error::x\n6 1 S 00:01 ##[group]x\n7 1 \x1b[0m 00:01 sh\n"
    argv, _ = _snap(monkeypatch, rows)
    for line in argv:
        assert line.isprintable() and "\n" not in line
        assert not line.startswith(("::", "##["))
        assert "[" not in line and ";" not in line and "::" not in line


def test_a_long_name_is_truncated(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 S 00:01 " + "python " + "-v " * 500 + "\n")
    assert all(len(line) <= sa.ARGV_CHARS + 20 for line in argv)


def test_at_most_max_listed_rows(monkeypatch):
    rows = "".join(f"{n} 1 S 00:01 sleep\n" for n in range(100, 120))
    argv, _ = _snap(monkeypatch, rows)
    assert len(argv) == sa.MAX_LISTED


def test_a_bad_state_or_age_is_shown_as_unread_not_echoed(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 ::error:: 12:xx:zz sleep\n")
    assert argv == ("sleep ? ?",)


def test_ps_failing_gives_no_listing_and_never_raises(monkeypatch):
    assert _snap(monkeypatch, "", rc=2) == ((), ())
    assert _snap(monkeypatch, PS, stderr="boom") == ((), ())

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ps", 1)

    monkeypatch.setattr(vs.subprocess, "run", boom)
    assert vs.snapshot("x", 5.0) == ((), ())


def _acct(monkeypatch, before, left, snap=(("sleep S 00.01",), ("account-escaped",))):
    monkeypatch.setattr(
        va.VerifierAccount, "_escaped_count", lambda self, d=None, attempts=1: before
    )
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda self, n, d: left)
    monkeypatch.setattr(vs, "snapshot", lambda user, timeout: snap)
    return va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter"))


def test_reap_lists_what_it_counted(monkeypatch):
    got = _acct(monkeypatch, 2, 0).reap()
    assert got == 2 and got.argv == ("sleep S 00.01",) and got.reasons == ("account-escaped",)


def test_reap_counts_are_unchanged_for_every_branch(monkeypatch):
    assert _acct(monkeypatch, 0, 0).reap() == 0
    assert _acct(monkeypatch, 3, 0).reap(1) == 2  # main comes off a known count
    assert _acct(monkeypatch, 3, 0).reap(5) == 0
    assert _acct(monkeypatch, -1, 0).reap() == 1  # unknown is one more
    assert _acct(monkeypatch, 2, 1).reap() == 3  # something outlived the kills


def test_reap_with_nothing_counted_carries_no_listing(monkeypatch):
    got = _acct(monkeypatch, 1, 0).reap(1)
    assert got == 0 and got.argv == () and got.reasons == ()


def test_the_snapshot_is_not_taken_when_nothing_is_running(monkeypatch):
    calls = []
    acct = _acct(monkeypatch, 0, 0)
    monkeypatch.setattr(vs, "snapshot", lambda u, t: calls.append(u) or ((), ()))
    acct.reap()
    assert calls == []


def test_merge_keeps_both_listings():
    swept = sa.Survivors(1, ("pytest",), ("marked",))
    escaped = sa.Survivors(2, ("sleep S 00.01",), ("account-escaped",))
    both = swept + escaped
    assert both == 3 and both.argv == ("pytest", "sleep S 00.01")
    assert both.reasons == ("marked", "account-escaped")
    plain = sa.Survivors(0) + 2
    assert plain == 2 and plain.argv == ()


def test_launchd_children_are_listed_after_the_others(monkeypatch):
    rows = "".join(f"{n} 1 S 00:01 sleep\n" for n in range(10, 20)) + "99 50 S 00:09 python\n"
    argv, _ = _snap(monkeypatch, rows)
    assert argv[0] == "python S 00.09"


def test_merged_listing_is_bounded_and_reasons_not_repeated():
    one = sa.Survivors(1, ("sleep",) * 5, ("account-escaped",))
    two = sa.Survivors(1, ("sh",) * 5, ("account-escaped",))
    both = one + two
    assert len(both.argv) == sa.MAX_LISTED and both.reasons == (
        "account-escaped",
        "listing=incomplete",  # #1851: the cut is now said; the reason is still not repeated
    )


def test_a_three_digit_day_age_is_shown(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 S 123-04:05:06 sleep\n")
    assert argv == ("sleep S 123-04.05.06",)


def test_snapshot_is_taken_before_the_kill(monkeypatch):
    order = []
    acct = _acct(monkeypatch, 2, 0)
    monkeypatch.setattr(
        va.VerifierAccount, "_kill_until_clean", lambda self, n, d: order.append("kill") or 0
    )
    monkeypatch.setattr(vs, "snapshot", lambda user, timeout: order.append("snap") or ((), ()))
    acct.reap()
    assert order == ["snap", "kill"]


def test_a_slow_snapshot_cannot_change_the_count(monkeypatch):
    """A snapshot that eats the time must not make the kill run out of its budget (#1824 review)."""
    clock = {"now": 0.0}
    monkeypatch.setattr(va.time, "monotonic", lambda: clock["now"])

    def counted(self, deadline=None, attempts=1):
        clock["now"] += 90.0
        return 1  # only the main run, which reap(1) ends on purpose

    def kill(self, attempts, deadline):
        clock["now"] += 26.0
        return -1 if clock["now"] > deadline else 0

    def slow_ps(argv, **kwargs):
        clock["now"] += min(100.0, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="9 8 S 00:01 sleep\n", stderr="")

    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", counted)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", kill)
    monkeypatch.setattr(vs.subprocess, "run", slow_ps)
    assert va.VerifierAccount(user="x", root=Path("/tmp/none")).reap(1) == 0


def test_snapshot_timeout_is_capped(monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vs.subprocess, "run", run)
    vs.snapshot("x", 120.0)
    vs.snapshot("x", 4.0)
    assert seen[0] <= 30 and seen[1] <= 1.0


def _argv_of(monkeypatch, platform):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vs.subprocess, "run", run)
    monkeypatch.setattr(vs.sys, "platform", platform)
    vs.snapshot("x", 4.0)
    return seen[0]


def test_ps_argv_has_x_on_darwin_only(monkeypatch):
    """The platform branch of verifier_seen (macOS needs -x to list other sessions' processes)."""
    assert "-x" in _argv_of(monkeypatch, "darwin")
    assert "-x" not in _argv_of(monkeypatch, "linux")
