"""#814: the reason for a run that left processes behind names what they ran (the first words of
argv, masked as the P2P log is, cut short); the count still decides, and the rule is not relaxed."""

import os
import subprocess
import sys
import uuid
from typing import Any

import pytest

from cairnwork import confined, reasons, survivor_argv
from cairnwork.deferred_tests import base_comparison

LEAK = (
    "import os, pathlib, sys, time; os.setsid(); pathlib.Path(sys.argv[2]).touch(); time.sleep(60)"
)


def test_a_secret_argument_is_masked_before_the_cut():
    assert survivor_argv.prefix("srv --password hunter2 --port 80") == "srv --password <redacted>"
    assert "hunter2" not in survivor_argv.prefix("GITHUB_TOKEN=hunter2 srv go")
    assert survivor_argv.prefix("") == survivor_argv.UNREAD


def test_a_long_argument_is_cut_and_a_newline_cannot_start_a_line():
    out = survivor_argv.prefix("srv " + "x" * 500 + "\n::error::boom")
    assert len(out) <= survivor_argv.ARGV_CHARS + 3 and "\n" not in out


def test_survivors_stays_an_int_and_keeps_argv_through_a_sum():
    got = survivor_argv.Survivors(1, ("srv",)) + 2
    assert got == 3 and got.argv == ("srv",) and isinstance(got, int)
    assert survivor_argv.listing(got) == " (argv: srv)" and survivor_argv.listing(3) == ""


def test_only_the_count_decides_the_reason_stays_the_same_without_argv():
    assert reasons.why_not_passed(**_run(survivors=2)).endswith("was unreadable")
    named = reasons.why_not_passed(**_run(survivors=survivor_argv.Survivors(1, ("srv --x",))))
    assert named.endswith("was unreadable (argv: srv --x)")


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
    assert "-c" in survivors.argv[0] and sys.executable in survivors.argv[0]
    assert "leaky_marker" not in survivors.argv[0]  # third word is the cut
    assert subprocess.run(["/usr/bin/pgrep", "-f", "leaky_marker"], check=False).returncode == 1


def test_the_base_verdict_detail_lists_the_argv():
    class Run:
        returncode, forged, timed_out, mutated = 0, False, False, False
        survivors = survivor_argv.Survivors(1, ("srv --serve",))
        report_too_big = log_too_big = False
        collect_errors, teardown_errors, recorded, outcomes = (), (), True, {}
        spec = type("S", (), {"verifier_id": "v"})()

    got = base_comparison([Run()], frozenset({"t.py::test_x"}))
    assert got.kind == "UNCOMPARABLE" and got.code == "BASE_SURVIVORS"
    assert "BASE_SURVIVORS (exit 0)" in got.detail and "(argv: srv --serve)" in got.detail


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
    assert survivors >= 1 and any(a.startswith("/bin/sh -c") for a in survivors.argv)
