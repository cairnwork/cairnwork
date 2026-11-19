"""#1256: the shared child runner kills, caps and refuses; it never reports a partial success."""

import os
import sys
import time

import pytest

from cairnwork.child_run import run_child

pytestmark = pytest.mark.sandbox


def _py(code):
    return [sys.executable, "-I", "-c", code]


def test_a_child_that_answers_returns_its_output():
    out, timed_out, rc = run_child(
        _py("import sys; sys.stdout.write(sys.stdin.read().upper())"),
        b"abc",
        timeout=10,
        output_cap=100,
    )
    assert (out, timed_out, rc) == (b"ABC", False, 0)


def test_a_child_that_runs_too_long_is_killed_and_flagged():
    started = time.monotonic()
    out, timed_out, rc = run_child(
        _py("import time; time.sleep(60)"), b"", timeout=0.3, output_cap=100
    )
    assert timed_out and out == b""
    assert time.monotonic() - started < 10


def test_output_over_the_cap_is_a_failure_not_a_truncated_success():
    out, timed_out, rc = run_child(
        _py("import sys; sys.stdout.write('x' * 5000)"), b"", timeout=10, output_cap=100
    )
    assert (out, timed_out, rc) == (b"", False, 1)


def test_a_child_that_never_reads_a_large_input_still_ends():
    started = time.monotonic()
    _, timed_out, _ = run_child(
        _py("import time; time.sleep(60)"), b"y" * (4 << 20), timeout=0.3, output_cap=100
    )
    assert timed_out and time.monotonic() - started < 10


def test_a_grandchild_in_the_group_is_killed_with_the_child(tmp_path):
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pidfile)!r}, 'w').write(str(p.pid)); time.sleep(60)"
    )
    run_child(_py(code), b"", timeout=1.5, output_cap=100)
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("the grandchild survived the group kill")


def test_an_unlaunchable_child_is_a_failure():
    out, timed_out, rc = run_child(["/nonexistent/x"], b"", timeout=1, output_cap=10)
    assert (out, timed_out, rc) == (b"", False, 1)
