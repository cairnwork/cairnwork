"""#1796: the orphan cleanup records an attempt as cleaned only when every step is confirmed:
the process sweep shows nothing left, the scratch is gone, and the marker really went. Anything
else keeps the marker, lists the attempt under "kept", and the `cleanup` command exits 1."""

import json
import os
import subprocess
import sys

import pytest

from cairnwork import confined, proc_start, verify_cli
from cairnwork import verify_candidate as module
from cairnwork.confined import ATTEMPT_ENV, UNLISTED, Sweep
from cairnwork.intake import Intake

AID = "a0123456789abcdef"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root removes any directory")


def _orphan(state):
    (state / "verify-runs").mkdir(parents=True)
    marker = state / "verify-runs" / AID
    marker.write_text("")
    scratch = state / "verify-scratch" / AID
    scratch.mkdir(parents=True)
    (scratch / "f").write_text("x")
    return marker, scratch


def _sweeps(monkeypatch, result):
    monkeypatch.setattr(module, "sweep_marked", lambda *a, **k: result)


@pytest.mark.parametrize(
    "result",
    [UNLISTED, Sweep(listed=True, denied=1), Sweep(listed=True, unreadable=2)],
    ids=["table-unreadable", "kill-refused", "environment-hidden"],
)
def test_a_sweep_that_does_not_show_the_processes_gone_keeps_the_marker(
    tmp_path, monkeypatch, result
):
    marker, scratch = _orphan(tmp_path)
    _sweeps(monkeypatch, result)
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == []
    assert out["kept"] == [AID]
    assert marker.exists()


def test_the_kept_attempt_is_cleaned_by_a_later_run_once_the_sweep_is_clean(tmp_path, monkeypatch):
    marker, _ = _orphan(tmp_path)
    _sweeps(monkeypatch, UNLISTED)
    module.cleanup(Intake(tmp_path))
    _sweeps(monkeypatch, Sweep(listed=True))
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def test_a_marker_that_cannot_be_removed_is_kept_and_does_not_abort_cleanup(tmp_path):
    marker, scratch = _orphan(tmp_path)
    os.chmod(marker.parent, 0o500)  # the marker file can be opened, but not unlinked
    try:
        out = module.cleanup(Intake(tmp_path))
        assert out["cleaned"] == []
        assert out["kept"] == [AID]
        assert marker.exists()
    finally:
        os.chmod(marker.parent, 0o700)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}


def test_a_scratch_path_below_a_file_is_absent_and_the_attempt_is_cleaned(tmp_path):
    """Deliberate (#1796): nothing can exist below a regular file, so the scratch is gone."""
    marker, scratch = _orphan(tmp_path)
    scratch.joinpath("f").unlink()
    scratch.rmdir()
    scratch.parent.rmdir()
    scratch.parent.write_text("not a directory")
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def _run_cleanup(tmp_path, capsys):
    code = verify_cli.main(["--state", str(tmp_path), "cleanup"])
    return code, json.loads(capsys.readouterr().out)


def test_the_cleanup_command_exits_1_when_an_attempt_is_kept(tmp_path, capsys):
    marker, scratch = _orphan(tmp_path)
    inner = scratch / "sub"
    inner.mkdir()
    (inner / "g").write_text("x")
    os.chmod(inner, 0o500)
    try:
        code, out = _run_cleanup(tmp_path, capsys)
    finally:
        os.chmod(inner, 0o700)
    assert code == 1
    assert out["kept"] == [AID]


def test_the_cleanup_command_exits_0_when_everything_is_cleaned(tmp_path, capsys):
    _orphan(tmp_path)
    code, out = _run_cleanup(tmp_path, capsys)
    assert code == 0
    assert out == {"cleaned": [AID]}


def _marker_taken_at(marker, mark):
    marker.write_text(mark)  # what a run writes into its marker when it starts


def _hidden_old_process(monkeypatch, started):
    """A same-account process whose environment cannot be read, started at `started` seconds."""
    monkeypatch.setattr(proc_start, "LINUX", True)
    monkeypatch.setattr(proc_start, "_boot_id", lambda proc: "boot1")
    monkeypatch.setattr(proc_start, "boot_start", lambda pid, proc="/proc": started)
    monkeypatch.setattr(proc_start, "boot_mark", lambda proc="/proc": "boot1 5000.0")  # now
    monkeypatch.setattr(confined, "_restricted", lambda: "")
    monkeypatch.setattr(confined, "_reread", lambda pids: None)
    row = (999999, 999999, os.getuid(), None)
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: [row])


def test_an_unreadable_process_older_than_the_run_does_not_keep_the_marker(tmp_path, monkeypatch):
    marker, _ = _orphan(tmp_path)
    _marker_taken_at(marker, "boot1 1000.0")
    _hidden_old_process(monkeypatch, 10.0)  # started long before the run
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def test_an_unreadable_process_newer_than_the_run_keeps_the_marker(tmp_path, monkeypatch):
    marker, _ = _orphan(tmp_path)
    _marker_taken_at(marker, "boot1 1000.0")
    _hidden_old_process(monkeypatch, 2000.0)  # started after the run began
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert marker.exists()


@pytest.mark.parametrize(
    "mark",
    ["boot1 1e18", "boot1 1e300", "boot1 99999999999", "boot1 5000.5", "boot1 inf", "boot1 nan"]
    + ["boot1 -5", "boot2 10.0", "boot1 10.0 extra", "boot1", "", "garbage", "boot1  10.0"],
)
def test_a_start_mark_that_cannot_be_trusted_keeps_the_marker(tmp_path, monkeypatch, mark):
    marker, _ = _orphan(tmp_path)
    _marker_taken_at(marker, mark)
    _hidden_old_process(monkeypatch, 10.0)  # old: only a trusted mark would let it be ignored
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert marker.exists()


def test_an_unreadable_process_keeps_the_marker_when_the_run_left_no_start_mark(
    tmp_path, monkeypatch
):
    marker, _ = _orphan(tmp_path)  # empty marker: macOS, or a run from before the mark
    _hidden_old_process(monkeypatch, 10.0)
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]


def test_a_run_writes_its_start_mark_into_its_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(proc_start, "boot_mark", lambda proc="/proc": "boot1 1000.0")
    with module._marked(Intake(tmp_path), AID):
        assert (tmp_path / "verify-runs" / AID).read_text() == "boot1 1000.0"


def _looks(monkeypatch, *results, then):
    queue = list(results)
    monkeypatch.setattr(module, "sweep_marked", lambda *a, **k: queue.pop(0) if queue else then)


def test_a_process_seen_only_on_the_second_look_keeps_the_marker(tmp_path, monkeypatch):
    marker, _ = _orphan(tmp_path)
    _looks(monkeypatch, Sweep(listed=True), then=Sweep(listed=True, denied=1))
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert marker.exists()


def test_two_clean_looks_in_a_row_after_unclean_ones_clean_the_attempt(tmp_path, monkeypatch):
    marker, _ = _orphan(tmp_path)
    unclean = Sweep(listed=True, killed=1)
    _looks(monkeypatch, unclean, unclean, then=Sweep(listed=True))
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


def test_a_marked_orphan_that_can_be_killed_is_killed_and_the_attempt_cleaned(tmp_path):
    marker, _ = _orphan(tmp_path)
    sleeper = "import time; time.sleep(60)"
    child = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {sleeper!r}])"
    env = {**os.environ, ATTEMPT_ENV: AID}
    subprocess.run([sys.executable, "-c", child], env=env, check=True)  # the grandchild is orphaned
    try:
        assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    finally:
        confined.sweep_marked(ATTEMPT_ENV, AID)
    assert not marker.exists()
