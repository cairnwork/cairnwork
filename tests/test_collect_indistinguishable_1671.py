"""#1671 (review of #1698): code under test cannot tell the quiet collect run from a real run.

The property, not a storage shape: a probe test module records a normalised observation of every
surface the code under test reaches (collect_probe_1671.py) and returns it as parametrize ids. It
runs once as the quiet collect run and once as a real run of the same file, with different run
ids, as in production (``<attempt>-fct0`` against ``<attempt>-b``). The two id sets must be equal.
The sensitivity controls run the same probe against deliberately leaky guard variants and must give
different sets, so the detector itself is pinned. Real runs under the sandbox, owner path."""

import sys
import time

import pytest
from collect_probe_1671 import LEAKS, PROBE
from conftest import commit_change

from cairnwork import report_channel, report_guard, verifiers
from cairnwork.collect_reader import read_collected
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext

sandbox = pytest.mark.sandbox
PATH = "tests/test_probe.py"
QUIET, REAL = ("x-fct0", True), ("x-b", False)


def _pair(repo, tmp_path, files=None, patch=None):
    """(quiet run, real run) of the probe over one commit; ``patch(quiet)`` runs before each."""
    head = commit_change(repo, files or {PATH: PROBE})
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    runs = []
    for run_id, quiet in (QUIET, REAL):
        if patch:
            patch(quiet)
        targets = tuple(files or (PATH,))
        spec = VerifierSpec("probe", targets, collect_only=True, announce=False)  # the quiet run
        spec = spec if quiet else VerifierSpec("probe", targets)  # a run that executes the tests
        (run,) = run_verifiers(ctx, head, (spec,), run_id)
        runs.append(run)
    return runs


def _differences(quiet, real):
    return sorted(set(quiet.collected) ^ set(real.collected))


@sandbox
def test_the_quiet_collect_run_and_a_real_run_show_the_code_under_test_the_same_surfaces(
    repo, tmp_path
):
    quiet, real = _pair(repo, tmp_path)
    assert len(real.collected) > 50 and real.ran, real.why  # the probe saw something and ran
    assert not quiet.outcomes and quiet.observation.passed is not True
    assert quiet.returncode == 0 and quiet.recorded and quiet.collection_finished
    assert _differences(quiet, real) == []
    assert read_collected(quiet).ids and not read_collected(quiet).unusable


def _leaky(monkeypatch, code, current):
    """Install ``code`` at the end of the guard module and hand its bit over in LEAKBIT."""
    install = report_guard.install_plugin
    prepare = verifiers._prepare_run

    def leaky_install(directory, data_paths=()):
        install(directory, data_paths)
        with (directory / f"{report_guard.MODULE}.py").open("a") as handle:
            handle.write("\n" + code)

    def leaky_prepare(*args, **kwargs):
        got = prepare(*args, **kwargs)
        got[3]["LEAKBIT"] = "q" if current["quiet"] else "r"
        return got

    monkeypatch.setattr(report_guard, "install_plugin", leaky_install)
    monkeypatch.setattr(verifiers, "_prepare_run", leaky_prepare)


@sandbox
@pytest.mark.parametrize("shape", LEAKS)
def test_the_probe_sees_a_bit_a_leaky_guard_would_leak(repo, tmp_path, monkeypatch, shape):
    """Sensitivity control: each way of keeping a quiet/real bit in the child gives another set."""
    current = {"quiet": True}
    _leaky(monkeypatch, LEAKS[shape], current)
    quiet, real = _pair(repo, tmp_path, patch=lambda q: current.update(quiet=q))
    assert read_collected(quiet).ids, quiet.why  # the control itself ran as a collect run
    assert _differences(quiet, real), f"the probe did not see {shape}"


@sandbox
def test_the_probe_sees_a_directory_name_that_says_which_run_it_is(repo, tmp_path, monkeypatch):
    """Sensitivity control for the pre-#1698 names: the run id (``fct0`` or ``b``) in the path."""
    current = {"quiet": True}
    monkeypatch.setattr(
        verifiers,
        "opaque_clone_id",
        lambda attempt_id: f"k{'fct' if current['quiet'] else 'b'}-v0",
    )
    quiet, real = _pair(repo, tmp_path, patch=lambda q: current.update(quiet=q))
    assert read_collected(quiet).ids, quiet.why
    assert _differences(quiet, real)


# ---- failure direction: nothing here lets a quiet run pass or read a wrong set ----------------


def _quiet(repo, tmp_path, files, timeout=600, run_id="x-fct0"):
    head = commit_change(repo, files)
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    spec = VerifierSpec("q", tuple(sorted(files)), collect_only=True, announce=False)
    start = time.monotonic()
    (run,) = run_verifiers(ctx, head, (spec,), run_id, timeout=timeout)
    return run, time.monotonic() - start


@sandbox
def test_a_child_that_exits_before_the_end_of_collection_is_unusable(repo, tmp_path):
    run, _ = _quiet(repo, tmp_path, {"tests/test_a.py": "import os\nos._exit(0)\n"})
    assert read_collected(run).unusable == "collect-not-recorded"
    assert run.observation.passed is not True and not run.recorded


@sandbox
def test_a_collection_that_never_ends_is_a_timeout_not_a_set(repo, tmp_path):
    body = "import time\ntime.sleep(300)\n\ndef test_a():\n    pass\n"
    run, _ = _quiet(repo, tmp_path, {"tests/test_a.py": body}, timeout=4)
    assert run.timed_out and read_collected(run).unusable == "collect-not-run"


@sandbox
def test_a_child_that_never_exits_after_collection_is_ended_by_the_parent(repo, tmp_path):
    body = (
        "import subprocess, sys, time\n\ndef test_a():\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "    time.sleep(300)\n"
    )
    run, seconds = _quiet(repo, tmp_path, {"tests/test_a.py": body})
    got = read_collected(run)
    assert got.ids == ("tests/test_a.py::test_a",) and not got.unusable, got
    assert seconds < 90 and not run.timed_out and not run.survivors
    assert not run.outcomes and run.observation.passed is not True


@sandbox
def test_a_parent_that_cannot_read_the_frame_gets_a_run_it_refuses(repo, tmp_path, monkeypatch):
    def broken(self):
        raise OSError("cannot read")

    monkeypatch.setattr(report_channel.GuardChannel, "read_collected", broken)
    run, _ = _quiet(repo, tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    assert read_collected(run).unusable == "collect-ran-tests"  # it went on to run the tests
    assert run.observation.passed is not True


@sandbox
def test_a_guard_that_writes_no_collection_record_leaves_an_unusable_run(
    repo, tmp_path, monkeypatch
):
    install = report_guard.install_plugin

    def without_record(directory, data_paths=()):
        install(directory, data_paths)
        path = directory / f"{report_guard.MODULE}.py"
        text = path.read_text()
        assert "_write(self, record, self._cfd, self._cpath, _io)" in text
        path.write_text(text.replace("_write(self, record, self._cfd, self._cpath, _io)", "pass"))

    monkeypatch.setattr(report_guard, "install_plugin", without_record)
    run, _ = _quiet(repo, tmp_path, {"tests/test_a.py": "def test_a():\n    pass\n"})
    assert read_collected(run).unusable
    assert run.observation.passed is not True


@sandbox
def test_a_single_failing_test_that_ends_before_the_first_poll_is_not_refused(repo, tmp_path):
    files = {"tests/test_f.py": "def test_f():\n    assert False\n"}
    head = commit_change(repo, files)
    ctx = VerifyContext(repo, tmp_path / "runs", python=sys.executable)
    got = []
    for run_id, announce in (("x-fct0", False), ("x-fct1", True)):
        spec = VerifierSpec("c", tuple(files), collect_only=True, announce=announce)
        got.append(read_collected(run_verifiers(ctx, head, (spec,), run_id)[0]))
    assert [(g.ids, g.error_files) for g in got[:1]] == [(g.ids, g.error_files) for g in got[1:]]
    assert got[0].ids == ("tests/test_f.py::test_f",)
    assert not got[0].unusable
