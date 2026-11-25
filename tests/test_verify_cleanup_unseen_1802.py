"""#1802: what the cleanup cannot see is a named leftover on every Python version. pathlib raises
PermissionError for is_dir/is_symlink/is_file below a directory that cannot be searched on 3.11-3.13
(3.14 answers False), so each walk and each remover must not let that abort or read as "absent"."""

import os

import pytest
from run_root_scope import own_attempt
from test_recovery import GOOD, crash, project, stores  # noqa: F401 - project is a fixture

from cairnwork import verify_candidate as module
from cairnwork.intake import Intake
from cairnwork.recovery import UNSEARCHABLE, _leftover_names, _remove_leftovers, recover

AID = "a0123456789abcdef"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root reads and removes anything")


def _orphan(state):
    (state / "verify-runs").mkdir(parents=True)
    marker = state / "verify-runs" / AID
    marker.write_text("")
    (state / "verify-scratch" / AID).mkdir(parents=True)
    return marker


class _Account:
    def __init__(self, run_root):
        self.run_root = run_root

    def remove(self, path):
        raise AssertionError("must not be asked to remove behind an unsearchable parent")


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o400, 0o600])
@pytest.mark.parametrize("kind", ["work", "evidence_log"])
def test_an_unsearchable_directory_under_the_run_root_does_not_abort_cleanup(tmp_path, mode, kind):
    marker = _orphan(tmp_path)
    root = tmp_path / "runs"
    root.mkdir()
    if kind == "work":
        inner = root / f"{AID}-q-v0.work"
    else:
        inner = root / f"{AID}-x-v0.priv"
    inner.mkdir()
    (inner / ".evidence").write_text("x")
    os.chmod(inner, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
        _remove_leftovers(root, AID)  # what recover() calls: must not raise either
        seen = _leftover_names(root, AID)
    finally:
        os.chmod(inner, 0o700)
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"] == {AID: [UNSEARCHABLE]} or inner.name in out["leftovers"][AID]
    assert seen and (seen == [UNSEARCHABLE] or inner.name in seen)


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o400, 0o600])
def test_recover_on_an_unsearchable_run_root_counts_a_leftover(project, tmp_path, mode):  # noqa: F811
    crash(project, tmp_path, "WORKER_STARTED")
    attempt = own_attempt(tmp_path)
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    (root / f"{attempt}-v-v0").mkdir()
    os.chmod(root, mode)
    try:
        ledger, notary, journal = stores(tmp_path)
        (decision,) = recover(
            ledger=ledger, notary=notary, journal=journal, run_root=root,
        )  # fmt: skip
    finally:
        os.chmod(root, 0o700)
    assert "RECOVERY_ERROR" not in decision.detail
    assert decision.leftover_run_dirs >= 1 and UNSEARCHABLE in decision.detail


@pytest.mark.parametrize("parent_mode", [0o000, 0o400])
def test_an_account_root_behind_an_unsearchable_parent_keeps_the_attempt(
    tmp_path, monkeypatch, parent_mode
):
    marker = _orphan(tmp_path)
    parent = tmp_path / "acct"
    (parent / "runs").mkdir(parents=True)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: _Account(parent / "runs"))
    os.chmod(parent, parent_mode)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(parent, 0o700)
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"][AID]


def test_a_run_root_that_is_a_plain_file_is_cleaned(tmp_path):
    _orphan(tmp_path)
    (tmp_path / "runs").write_text("x")
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}


def test_no_verifier_account_leaves_the_result_unchanged(tmp_path, monkeypatch):
    _orphan(tmp_path)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: None)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}


# the root can be searched before and after, but the listing itself fails once in between
def _listing_fails(monkeypatch):
    import sys

    from cairnwork import recovery

    real = os.listdir

    def listdir(path="."):
        frame = sys._getframe(1)
        while frame is not None:
            if frame.f_code.co_name == "_run_dirs":
                raise PermissionError(13, "Permission denied", str(path))
            frame = frame.f_back
        return real(path)

    monkeypatch.setattr(recovery.os, "listdir", listdir)


def test_a_listing_that_fails_between_two_good_checks_is_a_leftover_not_clean(
    tmp_path, monkeypatch
):
    marker = _orphan(tmp_path)
    root = tmp_path / "runs"
    (root / f"{AID}-v-v0").mkdir(parents=True)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: None)  # only the owner's root
    _listing_fails(monkeypatch)
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"] == {AID: [UNSEARCHABLE]}
    assert _leftover_names(root, AID) == [UNSEARCHABLE]


def test_recover_listing_fails_between_checks_counts_a_leftover(project, tmp_path, monkeypatch):  # noqa: F811
    crash(project, tmp_path, "WORKER_STARTED")
    attempt = own_attempt(tmp_path)
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    (root / f"{attempt}-v-v0").mkdir()
    _listing_fails(monkeypatch)
    ledger, notary, journal = stores(tmp_path)
    (decision,) = recover(ledger=ledger, notary=notary, journal=journal, run_root=root)
    assert "RECOVERY_ERROR" not in decision.detail
    assert decision.leftover_run_dirs >= 1 and UNSEARCHABLE in decision.detail
