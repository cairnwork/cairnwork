"""#814: the reason for a run that left processes behind names what they ran (the first words of
argv, masked as the P2P log is, cut short); the count still decides, and the rule is not relaxed."""

import os
import subprocess
import sys
import time
import uuid
from typing import Any

import pytest

from cairnwork import confined, reasons, survivor_argv
from cairnwork.deferred_tests import base_comparison

LEAK = (
    "import os, pathlib, sys, time; os.setsid(); pathlib.Path(sys.argv[2]).touch(); time.sleep(60)"
)


def test_a_secret_argument_is_masked_before_the_cut():
    assert survivor_argv.prefix("sh --password FAKE-VALUE-1 --port 80") == "sh <redacted>"
    assert "FAKE-VALUE-1" not in survivor_argv.prefix("GITHUB_TOKEN=FAKE-VALUE-1 sh go")
    assert survivor_argv.prefix("") == survivor_argv.UNREAD


def test_a_long_argument_is_cut_and_a_newline_cannot_start_a_line():
    out = survivor_argv.prefix("sh " + "x" * 500 + "\n::error::boom")
    assert len(out) <= survivor_argv.ARGV_CHARS + 3 and "\n" not in out


def test_survivors_stays_an_int_and_keeps_argv_through_a_sum():
    got = survivor_argv.Survivors(1, ("sh",)) + 2
    assert got == 3 and got.argv == ("sh",) and isinstance(got, int)
    assert survivor_argv.listing(got) == " (argv: sh)" and survivor_argv.listing(3) == ""


def test_only_the_count_decides_the_reason_stays_the_same_without_argv():
    assert reasons.why_not_passed(**_run(survivors=2)).endswith("was unreadable")
    named = reasons.why_not_passed(**_run(survivors=survivor_argv.Survivors(1, ("sh -x",))))
    assert named.endswith("was unreadable (argv: sh -x)")


def _run(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ran_sandboxed": True, "returncode": 0, "timed_out": False, "mutated": (),
        "survivors": 0, "log_too_big": False, "tests_ran": True,
    }  # fmt: skip
    return base | over


def test_a_real_leaked_process_is_named_and_still_counted(tmp_path):
    if not sys.platform.startswith(("linux", "darwin")):
        pytest.skip("needs /proc or ps")
    script = tmp_path / "leaky_server.py"
    flag = (
        tmp_path / "left-the-group"
    )  # the child says so: killed before setsid, it would not count
    script.write_text(
        "import subprocess, sys, time, pathlib\n"
        f"subprocess.Popen([sys.executable, '-c', {LEAK!r}, 'leaky_marker', {str(flag)!r}])\n"
        f"while not pathlib.Path({str(flag)!r}).exists():\n    time.sleep(0.05)\n"
    )
    env = {"PATH": os.environ["PATH"], confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    _rc, _timed_out, survivors = confined.execute(
        [sys.executable, str(script)], tmp_path, env, 30, tmp_path / "log"
    )
    assert survivors == 1  # the rule is unchanged: one survivor
    shown = survivors.argv[0]  # macOS ps names the framework binary, not the venv link
    assert shown != survivor_argv.UNREAD and "python" in shown.lower()
    assert "leaky_marker" not in survivors.argv[0]  # third word is the cut
    assert subprocess.run(["/usr/bin/pgrep", "-f", "leaky_marker"], check=False).returncode == 1


def test_the_base_verdict_detail_lists_the_argv():
    class Run:
        returncode, forged, timed_out, mutated = 0, False, False, False
        survivors = survivor_argv.Survivors(1, ("sh --serve",))
        report_too_big = log_too_big = False
        collect_errors, teardown_errors, recorded, outcomes = (), (), True, {}
        spec = type("S", (), {"verifier_id": "v"})()

    got = base_comparison([Run()], frozenset({"t.py::test_x"}))
    assert got.kind == "UNCOMPARABLE" and got.code == "BASE_SURVIVORS"
    assert "BASE_SURVIVORS (exit 0)" in got.detail and "(argv: sh --serve)" in got.detail


def test_a_platform_shell_found_by_its_directory_is_named_too(tmp_path):
    # macOS withholds /bin/sh's environment, so the sweep finds it by its working directory
    # (proc_cwd); its argv is still read before the kill. The #799 gunicorn-teardown pattern.
    script = tmp_path / "teardown.py"
    script.write_text("import os, subprocess\nsubprocess.Popen(['/bin/sh', '-c', 'sleep 60; true'],"
                      " preexec_fn=os.setsid)\n")  # fmt: skip
    env = {"PATH": os.environ["PATH"], confined.RUN_TOKEN_VAR: uuid.uuid4().hex}
    *_, survivors = confined.execute(
        [sys.executable, str(script)], tmp_path, env, 30, tmp_path / "log"
    )
    assert survivors >= 1 and any(a.startswith("sh -c") for a in survivors.argv)


def test_the_raw_argv_is_cut_before_it_is_masked_so_a_huge_argument_is_quick():
    started = time.monotonic()
    out = survivor_argv.prefix("sh " + "a-" * 500_000)
    assert time.monotonic() - started < 2 and len(out) <= survivor_argv.ARGV_CHARS + 3


def test_a_value_near_the_cut_is_not_shown():
    out = survivor_argv.prefix("x" * 55 + " --password FAKE-VALUE-1")
    assert "FAKE-VALUE-1" not in out and out == "<redacted>"


def test_a_runner_with_an_unsupported_major_version_is_hidden_whole():
    for command in ("python4 -m pytest", "ruby1.9 x"):
        assert survivor_argv.prefix(command) == survivor_argv.HIDDEN


def test_a_module_name_is_shown_only_after_minus_m():
    assert survivor_argv.prefix("python -v pytest") == "python -v <redacted>"


@pytest.mark.parametrize("text", [
    "sh --unlisted-option=FAKE-VALUE-1 go",  # long option the old rules did not name
    "sh --unlisted-option FAKE-VALUE-1",
    "sh -zFAKE-VALUE-1",  # short flag with the value attached
    "sh -Z FAKE-VALUE-1",
    "sh 'FAKE-VALUE-1 two' more",  # a value with a space in it
    "sh FAKE-VALUE-1",  # positional
    "python3 -m pytest FAKE-VALUE-1",
    "curl -u user:FAKE-VALUE-1 http://h",  # program not on the list
    "/opt/FAKE-VALUE-1/bin/tool run",
    "sh -c FAKE-VALUE-1",
    "sh -x -v FAKE-VALUE-1",
])  # fmt: skip
def test_nothing_unlisted_is_shown(text):
    assert "FAKE-VALUE-1" not in survivor_argv.prefix(text)
    assert "two" not in survivor_argv.prefix(text)


@pytest.mark.parametrize(("text", "shown"), [
    ("/usr/bin/python3.12 -m pytest tests/FAKE-VALUE-1", "python3.12 -m pytest"),
    ("/bin/sh -c sleep 60", "sh -c <redacted>"),
    ("node --serve", "node --serve"),
    ("tool run", "<redacted>"),
])  # fmt: skip
def test_a_known_runner_is_still_recognisable(text, shown):
    assert survivor_argv.prefix(text) == shown


def test_a_filter_error_hides_the_argv_and_does_not_fall_back(monkeypatch):
    def boom(words):
        raise RuntimeError("filter broke")

    monkeypatch.setattr(survivor_argv, "_allowed", boom)
    assert survivor_argv.prefix("sh -x FAKE-VALUE-1") == survivor_argv.ARGV_HIDDEN
    got = survivor_argv.Survivors(2, (survivor_argv.prefix("sh go"),))
    assert got == 2 and "hidden" in survivor_argv.listing(got)
    assert reasons.why_not_passed(**_run(survivors=got)).endswith("(argv: (argv hidden))")


def test_the_literal_limits_and_the_separator_between_prefixes():  # #836 item 1
    assert (survivor_argv.ARGV_ITEMS, survivor_argv.ARGV_CHARS) == (3, 80)
    assert survivor_argv.prefix("python3.12 -m pytest") == "python3.12 -m pytest"


def test_a_long_or_odd_runner_version_is_hidden_not_shown(monkeypatch):  # desk b r1 (Codex)
    for name in ("python" + "3" * 200, "python3.123", "python3.12.1", "ruby3.2.2", "python33"):
        assert survivor_argv.prefix(name + " -m pytest") == survivor_argv.HIDDEN, name
    for name in ("python", "python3", "python3.9", "python3.14", "ruby", "ruby3.2"):
        assert survivor_argv.prefix(name).startswith(name), name
    monkeypatch.setattr(survivor_argv, "ARGV_CHARS", 5)  # the cut still applies to what is shown
    assert survivor_argv.prefix("python3 -m pytest") == "pytho..."
    both = survivor_argv.Survivors(2, ("sh -x", "node --serve"))
    assert survivor_argv.listing(both) == " (argv: sh -x, node --serve)"


def test_the_count_the_reason_text_and_the_receipt_are_unchanged_by_the_filter():
    secret = survivor_argv.Survivors(2, (survivor_argv.prefix("sh --token FAKE-VALUE-1"),))
    plain = reasons.why_not_passed(**_run(survivors=2))
    named = reasons.why_not_passed(**_run(survivors=secret))
    assert secret == 2 and int(secret) == 2 and plain.startswith(named.split(" (argv")[0])
    assert "FAKE-VALUE-1" not in named


def test_the_failure_reason_never_carries_an_unlisted_argument():  # path 1: result.reason
    got = survivor_argv.Survivors(1, (survivor_argv.prefix("sh --unlisted=FAKE-VALUE-1 go"),))
    assert "FAKE-VALUE-1" not in reasons.why_not_passed(**_run(survivors=got))


def test_the_uncomparable_detail_never_carries_an_unlisted_argument():  # path 2: detail
    class Run:
        returncode, forged, timed_out, mutated = 0, False, False, False
        survivors = survivor_argv.Survivors(1, (survivor_argv.prefix("sh -zFAKE-VALUE-1 a b"),))
        report_too_big = log_too_big = False
        collect_errors, teardown_errors, recorded, outcomes = (), (), True, {}
        spec = type("S", (), {"verifier_id": "v"})()

    got = base_comparison([Run()], frozenset({"t.py::test_x"}))
    assert got.code == "BASE_SURVIVORS" and "(argv: sh <redacted>)" in got.detail
    assert "FAKE-VALUE-1" not in got.detail


def test_record_delimiters_are_replaced_in_each_prefix():
    out = survivor_argv.prefix("a[b]c;d:e")
    assert not set("[];:") & set(out)


def test_only_the_first_listed_processes_and_the_cut_length_count(monkeypatch):
    monkeypatch.setattr(survivor_argv, "read_argv", lambda pid: "python3 -m pytest")
    monkeypatch.setattr(survivor_argv, "ARGV_CHARS", 5)
    seen: dict[int, str] = {}
    for pid in range(survivor_argv.MAX_LISTED + 3):
        survivor_argv.note(seen, pid)
    assert len(seen) == survivor_argv.MAX_LISTED and set(seen) == set(
        range(survivor_argv.MAX_LISTED)
    )
    assert all(len(v) == survivor_argv.ARGV_CHARS + 3 for v in seen.values())
    survivor_argv.note(seen, 0)  # once each
    assert len(seen) == survivor_argv.MAX_LISTED


def test_a_non_utf8_argv_is_read_not_raised(monkeypatch):
    done = subprocess.CompletedProcess([], 0, stdout=b"sh \xff\xfe go\n")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(survivor_argv.subprocess, "run", lambda *a, **k: done)
    assert survivor_argv.prefix(survivor_argv.read_argv(1)) == "sh <redacted>"
