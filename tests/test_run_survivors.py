"""#578: a confined run's escape check (``confined._sweep_survivors``) counts every process it
cannot show gone. A marked process that exited before its kill (it may have left a child), one
whose environment stays hidden, and, on macOS, a marked platform binary (its environment is
withheld from ps; it is tied to the run by a working directory inside the checkout) each count;
a clean table still counts none. The process table is replaced by rows of this test's own child
or by synthetic rows, and os.kill is faked wherever a kill could reach a synthetic pid."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cairnwork import confined
from cairnwork.confined import Sweep

TOKEN = "run578-token"
MARKER = f"{confined.RUN_TOKEN_VAR}={TOKEN}"
FAKE = 4_000_578  # synthetic pids; never signalled for real
ME = os.getuid()


def _table(monkeypatch, rows, kill=None):
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: list(rows))
    monkeypatch.setattr(confined, "_reread", lambda pids: dict.fromkeys(pids))  # stays hidden
    monkeypatch.setattr(confined, "_restricted", lambda: "")
    monkeypatch.setattr(confined, "HIDDEN_WAIT", 0)
    if kill is not None:
        monkeypatch.setattr(confined.os, "kill", kill)


def _exited(_pid, _sig):
    raise ProcessLookupError


def test_a_marked_process_that_exited_before_its_kill_counts(monkeypatch):
    _table(monkeypatch, [(FAKE, FAKE, ME, {MARKER})], kill=_exited)
    assert confined._sweep_survivors(TOKEN, -1) == 1


def _looks(monkeypatch, *looks):
    """Each read of the process table returns the next look (the last repeats)."""
    calls = []

    def processes():
        calls.append(1)
        look = looks[min(len(calls), len(looks)) - 1]
        return None if look is None else list(look)

    monkeypatch.setattr(confined, "_processes_with_environment", processes)
    return calls


def test_a_child_left_by_a_process_that_exited_is_found_on_the_next_look(monkeypatch):
    killed = []

    def kill(pid, _sig):
        if pid == FAKE:
            raise ProcessLookupError  # the parent exited after the listing
        killed.append(pid)

    _table(monkeypatch, [], kill=kill)
    child = (FAKE + 1, FAKE + 1, ME, {MARKER})
    calls = _looks(monkeypatch, [(FAKE, FAKE, ME, {MARKER})], [child])
    assert confined._sweep_survivors(TOKEN, -1) == 1 and killed == [FAKE + 1] and len(calls) == 2


def test_a_process_that_exited_leaving_nothing_counts_none(monkeypatch):
    """A clean second look shows that the first missed nothing: a busy host's processes that
    end during the reads do not fail the run."""
    _table(monkeypatch, [], kill=_exited)
    calls = _looks(monkeypatch, [(FAKE, FAKE, ME, {MARKER})], [])
    assert confined._sweep_survivors(TOKEN, -1) == 0 and len(calls) == 2


def test_an_unreadable_table_counts_without_a_second_read(monkeypatch):
    """#114: a table that could not be read stays unknown; no second read waits for a cleaner
    answer (an escaped process could use that time)."""
    _table(monkeypatch, [])
    calls = _looks(monkeypatch, None, [])
    assert confined._sweep_survivors(TOKEN, -1) == 1 and len(calls) == 1


def test_a_process_whose_environment_stays_hidden_counts(monkeypatch):
    _table(monkeypatch, [(FAKE, FAKE, ME, None)])
    assert confined._sweep_survivors(TOKEN, -1) == 1


def test_a_killed_marked_process_counts_once_each(monkeypatch):
    killed = []
    rows = [(FAKE, FAKE, ME, {MARKER}), (FAKE + 1, FAKE + 1, ME, {MARKER})]
    _table(monkeypatch, rows, kill=lambda pid, _sig: killed.append(pid))
    assert confined._sweep_survivors(TOKEN, -1) == 2 and killed == [FAKE, FAKE + 1]


def test_a_clean_table_counts_no_survivor(monkeypatch):
    rows = [(FAKE, FAKE, ME, {"PATH=/bin"}), (FAKE + 1, FAKE + 1, ME, {f"{MARKER}0"})]
    _table(monkeypatch, rows, kill=lambda *_args: pytest.fail("nothing is marked"))
    assert confined._sweep_survivors(TOKEN, -1) == 0


def test_the_real_process_table_shows_a_fresh_token_gone(tmp_path):
    """On this host, with nothing started: the escape check counts no survivor (a busy host's
    hidden or withheld processes outside the fresh checkout do not count)."""
    assert confined._sweep_survivors(f"{TOKEN}-{os.getpid()}", -1, tmp_path) == 0


def test_execute_sweeps_with_the_checkout_and_the_start_mark(monkeypatch, tmp_path):
    seen = {}

    def sweep(var, value, **kwargs):
        seen.update(kwargs, var=var, value=value)
        return Sweep(listed=True)

    monkeypatch.setattr(confined.proc_start, "boot_mark", lambda: "boot-1 12.5")
    monkeypatch.setattr(confined, "sweep_marked", sweep)
    env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: TOKEN}
    returncode, timed_out, survivors = confined.execute(
        ["/usr/bin/true"], tmp_path, env, 30, tmp_path / "log"
    )
    assert (returncode, timed_out, survivors) == (0, False, 0)
    assert seen["value"] == TOKEN and seen["dispatched"] == "boot-1 12.5"
    assert seen["cwd_dirs"] == (os.path.realpath(tmp_path),)


@pytest.fixture
def own_platform_sleep(tmp_path):
    """This test's own /bin/sleep with the run marker, in a checkout-like directory; killed and
    reaped here whatever the sweep did."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        ["/bin/sleep", "60"],
        cwd=checkout,
        env={"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: TOKEN},
        start_new_session=True,
    )
    try:
        yield child, checkout
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS withholds platform binaries' env")
def test_a_marked_platform_binary_in_the_checkout_counts(own_platform_sleep, monkeypatch):
    child, checkout = own_platform_sleep
    done = subprocess.run(  # noqa: S603 - fixed argv, an integer pid
        ["/bin/ps", "-wwE", "-o", confined._PS_COLUMNS, "-p", str(child.pid)],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = confined._parse_ps(done.stdout)
    assert [r[0] for r in rows] == [child.pid] and MARKER not in (rows[0][3] or ())
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: rows)
    assert confined._sweep_survivors(TOKEN, -1, Path(checkout)) == 1
    assert child.wait(timeout=10) == -9  # the sweep ended it
