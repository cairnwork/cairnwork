"""#1796: the orphan cleanup records an attempt as cleaned only when every step is confirmed:
the process sweep shows nothing left, the scratch is gone, and the marker really went. Anything
else keeps the marker, lists the attempt under "kept", and the `cleanup` command exits 1."""

import json
import os

import pytest

from cairnwork import verify_candidate as module
from cairnwork import verify_cli
from cairnwork.confined import UNLISTED, Sweep
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
