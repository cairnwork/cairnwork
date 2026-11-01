"""#578: a confined run's escape check (``confined._sweep_survivors``) counts every process it
cannot show gone. A marked process that exited before its kill (it may have left a child), one
whose environment stays hidden, and, on macOS, a marked platform binary (its environment is
withheld from ps; it is tied to the run by a working directory inside the checkout) each count;
a clean table still counts none. The process table is replaced by rows of this test's own child
or by synthetic rows, and os.kill is faked wherever a kill could reach a synthetic pid. #599: none
counts only after two clean looks in a row; a reported working directory inside the run's counts
even if it now resolves elsewhere; the cwd path also runs where the host shows the environment."""

import os
import subprocess
import sys

import pytest

from cairnwork import confined, proc_cwd, proc_start
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
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
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
    calls = _looks(monkeypatch, [(FAKE, FAKE, ME, {MARKER})], [child], [])
    assert confined._sweep_survivors(TOKEN, -1) == 1 and killed == [FAKE + 1]
    assert len(calls) == 4  # the child killed at the second look, then two clean looks (#599)


def test_a_process_that_exited_leaving_nothing_counts_none(monkeypatch):
    """Two clean looks in a row show that the first missed nothing: a busy host's processes that
    end during the reads do not fail the run."""
    _table(monkeypatch, [], kill=_exited)
    calls = _looks(monkeypatch, [(FAKE, FAKE, ME, {MARKER})], [])
    assert confined._sweep_survivors(TOKEN, -1) == 0 and len(calls) == 3


def test_one_clean_look_is_not_enough(monkeypatch):
    """#599: as recovery (confirm_gone), the run counts none only after two clean looks in a row;
    a marked process listed only at the second look is killed and counted."""
    killed = []
    _table(monkeypatch, [], kill=lambda pid, _sig: killed.append(pid))
    _looks(monkeypatch, [], [(FAKE, FAKE, ME, {MARKER})], [])
    assert confined._sweep_survivors(TOKEN, -1) == 1 and killed == [FAKE]


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
    # still listed at every look (SIGKILL delivered is not a death): each counts once
    assert confined._sweep_survivors(TOKEN, -1) == 2 and killed[:2] == [FAKE, FAKE + 1]
    assert set(killed) == {FAKE, FAKE + 1}


def test_a_clean_table_counts_no_survivor(monkeypatch):
    rows = [(FAKE, FAKE, ME, {"PATH=/bin"}), (FAKE + 1, FAKE + 1, ME, {f"{MARKER}0"})]
    _table(monkeypatch, rows, kill=lambda *_args: pytest.fail("nothing is marked"))
    assert confined._sweep_survivors(TOKEN, -1) == 0


def test_the_real_process_table_shows_a_fresh_token_gone(tmp_path):
    """On this host, with nothing started: the escape check counts no survivor (a busy host's
    hidden processes that predate the mark, as execute takes it, or withheld ones outside the
    fresh checkout do not count)."""
    token, mark = f"{TOKEN}-{os.getpid()}", proc_start.boot_mark()
    assert confined._sweep_survivors(token, -1, (os.path.realpath(tmp_path),), mark) == 0


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
    rows = _only(child.pid, monkeypatch)
    assert MARKER not in (rows[0][3] or ())
    assert confined._sweep_survivors(TOKEN, -1, (os.path.realpath(checkout),)) == 1
    assert child.wait(timeout=10) == -9  # the sweep ended it


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS withholds platform binaries' env")
def test_a_checkout_swapped_for_a_symlink_keeps_the_sweep_where_the_run_started(
    tmp_path, monkeypatch
):
    """The run may replace its checkout with a symlink to another directory; the sweep still
    looks only in the directory the run started in, so a process working elsewhere is left be."""
    checkout, outside = tmp_path / "checkout", tmp_path / "outside"
    checkout.mkdir()
    outside.mkdir()
    child = subprocess.Popen(["/bin/sleep", "60"], cwd=outside, start_new_session=True)  # noqa: S603
    try:
        _only(child.pid, monkeypatch)
        env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: TOKEN}
        swap = ["/bin/sh", "-c", "cd .. && mv checkout moved && ln -s outside checkout"]
        assert confined.execute(swap, checkout, env, 30, tmp_path / "log")[0] == 0
        assert checkout.is_symlink()
        with pytest.raises(subprocess.TimeoutExpired):  # a SIGKILL would end it within this
            child.wait(timeout=2)
    finally:
        child.kill()
        child.wait()


def _only(pid: int, monkeypatch) -> list:
    """The real ps row of this test's own child, as the only process the sweep can see; skips
    where macOS shows the child's environment (some CI runners), so the cwd path is not used."""
    if not proc_cwd.environment_withheld(pid):
        pytest.skip("this host shows platform binaries' environment")
    done = subprocess.run(  # noqa: S603 - fixed argv, an integer pid
        ["/bin/ps", "-wwE", "-o", confined._PS_COLUMNS, "-p", str(pid)],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = confined._parse_ps(done.stdout)
    assert [r[0] for r in rows] == [pid]
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: rows)
    return rows


@pytest.fixture
def withheld_here(monkeypatch):
    """Stand-in for a host that withholds platform binaries' environment (#599): proc_cwd answers
    "withheld" for this test's own child only (``add``), so CI runners that show it cover the
    working-directory path too."""
    own: set[int] = set()
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: pid in own)
    return own.add


@pytest.mark.skipif(sys.platform != "darwin", reason="cwd_of reads macOS proc_pidinfo")
def test_a_platform_binary_in_the_checkout_counts_where_its_environment_reads_withheld(
    own_platform_sleep, monkeypatch, withheld_here
):
    child, checkout = own_platform_sleep
    withheld_here(child.pid)
    rows = [(child.pid, child.pid, ME, set(child.args))]  # as ps shows it: no environment
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: list(rows))
    assert confined._sweep_survivors(TOKEN, -1, (os.path.realpath(checkout),)) == 1
    assert child.wait(timeout=10) == -9  # the sweep ended it


@pytest.mark.skipif(sys.platform != "darwin", reason="cwd_of reads macOS proc_pidinfo")
def test_a_swapped_checkout_keeps_the_sweep_in_place_where_the_environment_reads_withheld(
    tmp_path, monkeypatch, withheld_here
):
    checkout, outside = tmp_path / "checkout", tmp_path / "outside"
    checkout.mkdir()
    outside.mkdir()
    child = subprocess.Popen(["/bin/sleep", "60"], cwd=outside, start_new_session=True)  # noqa: S603
    try:
        withheld_here(child.pid)
        _only(child.pid, monkeypatch)
        env = {"PATH": "/usr/bin:/bin", confined.RUN_TOKEN_VAR: TOKEN}
        swap = ["/bin/sh", "-c", "cd .. && mv checkout moved && ln -s outside checkout"]
        assert confined.execute(swap, checkout, env, 30, tmp_path / "log")[0] == 0
        assert checkout.is_symlink()
        with pytest.raises(subprocess.TimeoutExpired):  # a SIGKILL would end it within this
            child.wait(timeout=2)
    finally:
        child.kill()
        child.wait()


@pytest.fixture
def cwd_child(tmp_path, monkeypatch):
    """This test's own sleeper, read as a withheld process (stand-in readers); ``where`` sets the
    working directory it reports. Killed and reaped here whatever the sweep did."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # noqa: S603
    reported = {}
    monkeypatch.setattr(proc_cwd, "_MACOS", True)
    monkeypatch.setattr(proc_cwd, "_started", lambda pid: b"start" if pid == child.pid else None)
    monkeypatch.setattr(proc_cwd, "environment_withheld", lambda pid: pid == child.pid)
    monkeypatch.setattr(proc_cwd, "cwd_of", lambda pid: reported.get(pid))
    try:
        yield child, lambda path: reported.update({child.pid: path})
    finally:
        child.kill()
        child.wait()


def test_a_reported_directory_that_now_resolves_elsewhere_counts(tmp_path, cwd_child):
    """#599: the process reports a working directory inside the run's; a symlink put there since
    makes that path resolve outside. The reported path inside counts, as its realpath does."""
    child, where = cwd_child
    top, outside = os.path.realpath(tmp_path / "checkout"), tmp_path / "outside"
    os.mkdir(top)
    outside.mkdir()
    os.symlink(outside, os.path.join(top, "sub"))
    where(os.path.join(top, "sub"))
    found = proc_cwd.kill_withheld_in([(child.pid, child.pid, ME, set())], (top,), ME)
    assert found == ([child.pid], [], 0)
    assert child.wait(timeout=10) == -9


@pytest.mark.parametrize("spelling", ["{top}/../outside", "{top}/./../outside", "outside"])
def test_a_reported_directory_inside_only_by_its_spelling_is_left_alone(
    tmp_path, cwd_child, spelling
):
    """The reported path counts only as an absolute path without "." or "..": one that names a
    directory outside the run's by climbing out of it is judged by its realpath alone."""
    child, where = cwd_child
    top = os.path.realpath(tmp_path / "checkout")
    os.mkdir(top)
    (tmp_path / "outside").mkdir()
    where(spelling.format(top=top))
    found = proc_cwd.kill_withheld_in([(child.pid, child.pid, ME, set())], (top,), ME)
    assert found == ([], [], 0)
    assert child.poll() is None
