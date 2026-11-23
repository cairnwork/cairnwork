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


_HOG = "x = bytearray(300 << 20); x[::4096] = b'1' * len(x[::4096]); import time; time.sleep(60)"


def test_a_child_over_the_memory_limit_is_killed_and_refused():
    started = time.monotonic()
    out, timed_out, rc = run_child(
        _py(_HOG), b"", timeout=60, output_cap=100, memory_limit=100 << 20
    )
    assert (out, timed_out, rc) == (b"", False, 1)
    assert time.monotonic() - started < 20


def test_an_answer_written_before_going_over_the_limit_is_not_read_as_success():
    code = "import sys; sys.stdout.write('ok'); sys.stdout.flush(); " + _HOG
    assert run_child(_py(code), b"", timeout=60, output_cap=100, memory_limit=100 << 20) == (
        b"",
        False,
        1,
    )


def test_a_child_under_the_memory_limit_answers_as_before():
    code = "import sys; sys.stdout.write(sys.stdin.read())"
    got = run_child(_py(code), b"abc", timeout=10, output_cap=100, memory_limit=1 << 30)
    assert got == (b"abc", False, 0)


def test_a_grandchild_counts_toward_the_memory_limit(tmp_path):
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {_HOG!r}]); time.sleep(60)"
    )
    started = time.monotonic()
    got = run_child(_py(code), b"", timeout=60, output_cap=100, memory_limit=100 << 20)
    assert got == (b"", False, 1) and time.monotonic() - started < 20


def test_an_unreadable_memory_gauge_fails_closed(monkeypatch):
    monkeypatch.setattr("cairnwork.child_run._PS", "/nonexistent/ps")
    code = "import time; time.sleep(60)"
    started = time.monotonic()
    got = run_child(_py(code), b"", timeout=60, output_cap=100, memory_limit=1 << 30)
    assert got == (b"", False, 1) and time.monotonic() - started < 10


def test_a_gauge_that_hangs_does_not_outlast_the_time_limit(monkeypatch, tmp_path):
    ps = tmp_path / "ps"
    ps.write_text("#!/bin/sh\nexec sleep 60\n")
    ps.chmod(0o755)
    monkeypatch.setattr("cairnwork.child_run._PS", str(ps))
    started = time.monotonic()
    out, timed_out, rc = run_child(
        _py("import time; time.sleep(60)"), b"", timeout=1, output_cap=100, memory_limit=1 << 30
    )
    assert out == b"" and timed_out
    assert time.monotonic() - started < 6


def test_a_live_group_missing_from_the_gauge_fails_closed(monkeypatch, tmp_path):
    ps = tmp_path / "ps"
    ps.write_text("#!/bin/sh\necho '1 100'\n")  # a table without the child's group
    ps.chmod(0o755)
    monkeypatch.setattr("cairnwork.child_run._PS", str(ps))
    started = time.monotonic()
    got = run_child(
        _py("import time; time.sleep(60)"), b"", timeout=60, output_cap=100, memory_limit=1 << 30
    )
    assert got == (b"", False, 1) and time.monotonic() - started < 10


def test_a_gone_group_reads_as_zero():
    from cairnwork.child_run import _group_rss_bytes

    assert _group_rss_bytes(2**22 + 12345, 5) == 0
