"""#1819 diagnostics: processes the account-level reap() counts are listed (bounded argv prefix,
state, age, fixed reason ``account-escaped``). Text only: counts and verdicts stay as before.
"""

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cairnwork.survivor_argv as sa
import cairnwork.verifier_account as va
import cairnwork.verifier_escape as ve
import cairnwork.verifier_seen as vs

PS = "  10     1 S      01:02 /usr/bin/python3.13\n  11    10 Z+     00:03 sleep\n"


def _fake_run(stdout, rc=0, stderr=""):
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    return run


def _snap(monkeypatch, stdout, **kw):
    """(argv, reasons) the listing makes of one raw ps text (count = lines, nothing exempt)."""
    monkeypatch.setattr(vs.subprocess, "run", _fake_run(stdout, **kw))
    raw, _ = vs.before_kill("x", 1, time.monotonic() + 100.0)
    got = vs.result(max(len(stdout.splitlines()), 1), raw, None, 0, 1)
    return got.argv, got.reasons[:-1]  # the last reason is main=<n>


def test_rows_become_bounded_argv_prefixes_with_state_and_age(monkeypatch):
    argv, reasons = _snap(monkeypatch, PS)
    assert argv == ("sleep Z+ 00.03 ppid=10/python3.13", "python3.13 S 01.02 ppid=1/-")
    assert reasons == ("account-escaped",)


def test_a_candidate_chosen_name_is_hidden_not_echoed(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 S 00:01 /tmp/x/##[error]evil\n")
    assert argv == ("<redacted> S 00.01 ppid=1/-",)


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
    assert argv == ("sleep ? ? ppid=1/-",)


def test_ps_failing_gives_no_listing_and_never_raises(monkeypatch):
    assert _snap(monkeypatch, "", rc=2) == ((), ())
    assert _snap(monkeypatch, PS, stderr="boom") == ((), ())

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ps", 1)

    monkeypatch.setattr(vs.subprocess, "run", boom)
    assert vs.before_kill("x", 1, 500.0)[0] is None
    assert vs.result(2, None, None, 0, 1).reasons == ()


def _acct(monkeypatch, before, left, ps="9 1 S 00:01 sleep\n", **kw):
    monkeypatch.setattr(vs.subprocess, "run", _fake_run(ps, **kw))
    monkeypatch.setattr(vs.sys, "platform", "linux")
    monkeypatch.setattr(
        va.VerifierAccount, "_escaped_count", lambda self, d=None, attempts=1: before
    )
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda self, n, d: left)
    return va.VerifierAccount(user="x", root=Path("/tmp/does-not-matter"))


def test_reap_lists_what_it_counted(monkeypatch):
    got = _acct(monkeypatch, 2, 0).reap()
    assert got == 2 and got.argv == (
        "sleep S 00.01 ppid=1/-",
    )  # count 2, one entry: marked as incomplete
    assert got.reasons == ("account-escaped", "listing=incomplete", "main=0")


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
    monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: calls.append(a))
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
    assert argv[0] == "python S 00.09 ppid=50/-"


def test_merged_listing_is_bounded_and_reasons_not_repeated():
    one = sa.Survivors(1, ("sleep",) * 5, ("account-escaped",))
    two = sa.Survivors(1, ("sh",) * 5, ("account-escaped",))
    both = one + two
    assert len(both.argv) == sa.MAX_LISTED and both.reasons == ("account-escaped",)


def test_a_three_digit_day_age_is_shown(monkeypatch):
    argv, _ = _snap(monkeypatch, "5 1 S 123-04:05:06 sleep\n")
    assert argv == ("sleep S 123-04.05.06 ppid=1/-",)


def test_snapshot_is_taken_before_the_kill(monkeypatch):
    order = []
    acct = _acct(monkeypatch, 2, 0)
    monkeypatch.setattr(
        va.VerifierAccount, "_kill_until_clean", lambda self, n, d: order.append("kill") or 0
    )
    monkeypatch.setattr(
        vs.subprocess, "run", lambda *a, **k: order.append("snap") or _fake_run("")(*a, **k)
    )
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
    now = time.monotonic()
    vs.before_kill("x", 1, now + 120.0)
    vs.before_kill("x", 1, now + 4.0)
    assert seen[0] <= 30 and seen[1] <= 1.0


def test_the_parent_name_is_filtered_like_any_name(monkeypatch):
    rows = "20 1 S 00:09 /tmp/##[error]evil\n21 20 R 00:00 python\n"
    argv, _ = _snap(monkeypatch, rows)
    assert argv[0] == "python R 00.00 ppid=20/<redacted>"
    assert all("##[" not in line and "::" not in line for line in argv)


def test_reap_records_the_main_value_as_a_fixed_reason(monkeypatch):
    got = _acct(monkeypatch, 3, 0).reap(1)
    assert got.reasons[0] == "account-escaped" and got.reasons[-1] == "main=1"
    assert _acct(monkeypatch, 2, 0).reap().reasons[-1] == "main=0"
    assert _acct(monkeypatch, 1, 0, rc=2).reap().reasons == ()


def _argv_of(monkeypatch, platform):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vs.subprocess, "run", run)
    monkeypatch.setattr(vs.sys, "platform", platform)
    vs.before_kill("x", 1, time.monotonic() + 4.0)
    return seen[0]


def test_ps_argv_has_x_on_darwin_only(monkeypatch):
    """The platform branch of verifier_seen (macOS needs -x to list other sessions' processes)."""
    assert "-x" in _argv_of(monkeypatch, "darwin")
    assert "-x" not in _argv_of(monkeypatch, "linux")


def test_a_non_ascii_or_overlong_ppid_is_not_listed(monkeypatch):
    rows = "5 \u0663 S 00:01 sleep\n6 12345678901 S 00:01 sleep\n7 1 S 00:01 sleep\n"
    argv, _ = _snap(monkeypatch, rows)
    assert len(argv) == 1 and "ppid=1/" in argv[0]
    assert all("\u0663" not in line and "12345678901" not in line for line in argv)


def test_entries_are_built_only_for_the_listed_rows(monkeypatch):
    built = []
    real = vs._entry
    monkeypatch.setattr(vs, "_entry", lambda *a: built.append(a) or real(*a))
    rows = "".join(f"{n} 1 S 00:01 sleep\n" for n in range(100, 120))
    argv, _ = _snap(monkeypatch, rows)
    assert len(built) == sa.MAX_LISTED == len(argv)


EXEMPT = "/usr/libexec/exempt"


def _acct_rows(monkeypatch, before, main, rows, exempt=EXEMPT, paths=None):
    """reap() with a real snapshot over ``rows``; pid 10 is a launchd child that is exempt."""
    monkeypatch.setattr(vs.subprocess, "run", _fake_run(rows))
    monkeypatch.setattr(va.VerifierAccount, "_escaped_count", lambda s, d=None, attempts=1: before)
    monkeypatch.setattr(va.VerifierAccount, "_kill_until_clean", lambda s, n, d: 0)
    found = paths or (lambda s, pids, deadline=None: {p: (1.0, EXEMPT) for p in pids})
    monkeypatch.setattr(va.VerifierAccount, "_real_paths", found)
    monkeypatch.setattr(vs.sys, "platform", "darwin")
    monkeypatch.setattr(ve, "is_exempt", lambda path, ppid: path == exempt)
    return va.VerifierAccount(user="x", root=Path("/tmp/none")).reap(main)


def test_a_non_exempt_leftover_is_still_listed(monkeypatch):
    got = _acct_rows(monkeypatch, 1, 0, "20 15 S 00:01 python\n")
    assert got == 1 and got.argv and "python" in got.argv[0] and "account-escaped" in got.reasons


def test_an_exempt_daemon_is_not_listed_with_a_counted_process(monkeypatch):
    got = _acct_rows(monkeypatch, 1, 0, "10 1 S 00:09 launchd_thing\n20 15 S 00:01 python\n")
    assert got == 1 and len(got.argv) == 1 and "python" in got.argv[0]
    assert all("launchd_thing" not in line for line in got.argv)


def test_only_an_exempt_daemon_left_gives_no_listing(monkeypatch):
    got = _acct_rows(monkeypatch, 0, 0, "10 1 S 00:09 launchd_thing\n")
    assert got == 0 and got.argv == () and got.reasons == ()


def test_only_the_callers_main_left_is_not_listed_as_escaped(monkeypatch):
    got = _acct_rows(monkeypatch, 1, 1, "20 15 S 00:01 python\n")
    assert got == 0 and got.argv == () and "account-escaped" not in got.reasons


MAIN = "30 99 S 00:01 bash\n"  # the caller's own run: its parent is outside the account
LEFT = "20 1 S 00:02 python\n21 1 S 00:02 sleep\n"  # launchd children, not exempt


@pytest.mark.parametrize(
    ("before", "main", "rows", "names"),
    [
        (2, 1, MAIN + "20 1 S 00:02 python\n", ["python"]),
        (3, 1, MAIN + LEFT, ["python", "sleep"]),
        (2, 0, LEFT, ["python", "sleep"]),
    ],
)
def test_listing_never_has_more_entries_than_the_count(monkeypatch, before, main, rows, names):
    got = _acct_rows(monkeypatch, before, main, rows, exempt="none")
    assert got == before - main and len(got.argv) == len(names)
    assert all(n in line for n, line in zip(names, got.argv, strict=True))
    assert all(not line.startswith("bash") for line in got.argv)


def test_a_survivor_whose_parent_is_an_exempt_daemon_is_not_taken_for_main(monkeypatch):
    rows = "10 1 S 00:09 launchd_thing\n20 10 S 00:01 python\n" + MAIN
    got = _acct_rows(monkeypatch, 2, 1, rows)
    assert got == 1 and len(got.argv) == 1 and "python" in got.argv[0]
    assert all(not line.startswith("bash") for line in got.argv)


INCOMPLETE = "listing=incomplete"  # the one fixed marker: the listing and the count disagree
SEVEN = "".join(f"{50 + i} 1 S 00:02 p{i}\n" for i in range(7))


@pytest.mark.parametrize(
    ("before", "main", "rows", "count", "shown"),
    [
        (2, 1, MAIN + LEFT, 1, 1),  # ps shows more rows than the count: cut, and said so
        (2, 1, MAIN, 1, 0),  # nothing left after filtering although the count is above zero
        (3, 0, LEFT, 3, 2),  # ps shows fewer rows than the count
        (7, 0, SEVEN, 7, 5),  # the 5-row cap hides rows
        (5, 0, SEVEN, 5, 5),  # the cap hides rows although the lengths agree
    ],
)
def test_a_listing_that_disagrees_with_the_count_is_marked_incomplete(
    monkeypatch, before, main, rows, count, shown
):
    got = _acct_rows(monkeypatch, before, main, rows, exempt="none")
    assert got == count and len(got.argv) == shown  # the count and the cut stay as before
    assert INCOMPLETE in got.reasons and got.reasons[-1] == f"main={main}"


@pytest.mark.parametrize(
    ("before", "main", "rows"),
    [(2, 1, MAIN + "20 1 S 00:02 python\n"), (3, 1, MAIN + LEFT), (2, 0, LEFT)],
)
def test_a_listing_that_agrees_with_the_count_is_not_marked(monkeypatch, before, main, rows):
    got = _acct_rows(monkeypatch, before, main, rows, exempt="none")
    assert got == before - main and INCOMPLETE not in got.reasons


def test_a_failed_ps_stays_a_plain_count_with_no_marker(monkeypatch):
    got = _acct(monkeypatch, 2, 0, rc=2).reap()
    assert got == 2 and got.argv == () and got.reasons == ()


def _names(got):
    return [line.split()[0] for line in got.argv]


S1, S2 = (
    "20 98 S 00:02 python\n",
    "40 1 S 00:02 sleep\n",
)  # real leftovers; 20 has an outside parent


def test_two_rows_that_look_like_main_drop_nothing_and_say_so(monkeypatch):
    got = _acct_rows(monkeypatch, 2, 1, S1 + MAIN, exempt="none")  # count 1; M is pid 30
    assert got == 1 and sorted(_names(got)) == ["bash", "python"]
    assert "main=unresolved" in got.reasons


def test_a_survivor_with_an_outside_parent_is_never_hidden_behind_main(monkeypatch):
    got = _acct_rows(monkeypatch, 3, 1, S1 + MAIN + S2, exempt="none")  # count 2
    assert got == 2 and {"python", "sleep"} <= set(_names(got))
    assert "main=unresolved" in got.reasons


def test_a_paths_helper_failing_in_the_snapshot_does_not_hide_the_survivor(monkeypatch):
    rows = "10 1 S 00:09 launchd_thing\n11 1 S 00:01 python\n"
    got = _acct_rows(monkeypatch, 1, 0, rows, paths=lambda s, pids, deadline=None: {})
    assert got == 1 and "python" in _names(got) and "exempt=unknown" in got.reasons


def test_a_launchd_parent_candidate_is_not_guessed(monkeypatch):
    got = _acct_rows(monkeypatch, 2, 1, "20 1 S 00:02 python\n21 20 S 00:02 sleep\n", exempt="none")
    assert got == 1 and sorted(_names(got)) == ["python", "sleep"]
    assert "main=unresolved" in got.reasons


def test_exactly_one_main_candidate_is_dropped_and_names_are_pinned(monkeypatch):
    rows = MAIN + "".join(f"{40 + i} 1 S 00:02 p{i}\n" for i in range(7))  # 8 rows, main=1
    got = _acct_rows(monkeypatch, 8, 1, rows, exempt="none")
    assert got == 7 and len(got.argv) == 5 and "bash" not in _names(got)
    assert "main=unresolved" not in got.reasons


def test_main_alone_in_the_table_is_dropped_and_nothing_is_listed(monkeypatch):
    got = _acct_rows(monkeypatch, 2, 1, MAIN, exempt="none")  # count 1, nothing left to show
    assert got == 1 and got.argv == () and got.reasons == ("account-escaped", INCOMPLETE, "main=1")
