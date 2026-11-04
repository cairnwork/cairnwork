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
    assert survivors.argv[0].split()[0].startswith(sys.executable[: survivor_argv.ARGV_CHARS - 5])
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


def test_the_raw_argv_is_cut_before_it_is_masked_so_a_huge_argument_is_quick():
    started = time.monotonic()
    out = survivor_argv.prefix("srv " + "a-" * 500_000)
    assert time.monotonic() - started < 2 and len(out) <= survivor_argv.ARGV_CHARS + 3


def test_a_secret_in_the_third_word_near_the_cut_is_masked_not_split():
    # mask first, cut second: cut first would leave "--passwo..." or the start of the value
    text = "x" * 55 + " --password hunter2secretvalue"
    out = survivor_argv.prefix(text)
    assert "hunter2" not in out and "secretvalue" not in out and "<redacted>" in out


@pytest.mark.parametrize("text", [
    "curl -u bob:hunter2 http://h", "curl -a hunter2 x", "srv -p hunter2 y",
    "srv --password hunter2 more words", "srv --token=hunter2",
])  # fmt: skip
def test_credential_forms_in_the_first_words_are_masked(text):
    assert "hunter2" not in survivor_argv.prefix(text) and "more" not in survivor_argv.prefix(text)


def test_record_delimiters_are_replaced_in_each_prefix():
    out = survivor_argv.prefix("a[b]c;d:e")
    assert not set("[];:") & set(out)


def test_only_the_first_listed_processes_and_the_cut_length_count(monkeypatch):
    monkeypatch.setattr(survivor_argv, "read_argv", lambda pid: f"p{pid} " + "w" * 200)
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
    done = subprocess.CompletedProcess([], 0, stdout=b"srv \xff\xfe go\n")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(survivor_argv.subprocess, "run", lambda *a, **k: done)
    assert survivor_argv.prefix(survivor_argv.read_argv(1)).startswith("srv ")
