"""#1802: what the cleanup cannot see is a named leftover on every Python version. pathlib raises
PermissionError for is_dir/is_symlink/is_file below a directory that cannot be searched on 3.11-3.13
(3.14 answers False), so each walk and each remover must not let that abort or read as "absent"."""

import os

import pytest
from run_root_scope import own_attempt
from test_recovery import GOOD, crash, project, stores  # noqa: F401 - project is a fixture

from cairnwork import confined, recovery
from cairnwork import verify_candidate as module
from cairnwork.intake import Intake
from cairnwork.recovery import UNSEARCHABLE, _leftover_names, _remove_leftovers, recover

AID = "a0123456789abcdef"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root reads and removes anything")


@pytest.fixture(autouse=True)
def _no_other_processes(monkeypatch):
    """These tests are about files: another process of the host that hides its environment must
    not decide the sweep (the host's processes are not the attempt's)."""
    for owner in (module, recovery):
        if hasattr(owner, "sweep_marked"):
            monkeypatch.setattr(owner, "sweep_marked", lambda *a, **k: confined.Sweep(listed=True))


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


# #1802 round 3: a stat that fails (any error but "no such file") is "cannot see", never "absent"
_SHAPES = ("work", "clone", "index", "private")


def _lay(root, shape, ident=AID):
    root.mkdir(exist_ok=True)
    if shape == "work":
        (root / f"{ident}-s-v1.work").mkdir(exist_ok=True)
        (root / f"{ident}-s-v1.work" / "f").write_text("x")
    elif shape == "clone":
        (root / ident).mkdir(exist_ok=True)
        (root / ident / "f").write_text("x")
    elif shape == "index":
        (root / f"{ident}.index").write_text("x")
    else:
        (root / f"{ident}-s-v1.priv" / "run").mkdir(parents=True, exist_ok=True)


def _on_disk(root):
    return [n for n in os.listdir(root) if n.startswith(AID)] if root.exists() else []


def _stat_fails_once(monkeypatch, name, nth, root):
    real, count = getattr(os, name), [0]

    def flaky(path, *args, **kwargs):
        if str(path).startswith(str(root) + os.sep):
            count[0] += 1
            if count[0] == nth:
                raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, name, flaky)


@pytest.mark.parametrize("nth", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("name", ["lstat", "stat"])
@pytest.mark.parametrize("shape", _SHAPES)
def test_the_rescan_never_answers_empty_after_one_failed_stat(
    tmp_path, monkeypatch, shape, name, nth
):
    from cairnwork.verify_cleanup import leftovers

    root = tmp_path / "runs"
    _lay(root, shape)
    with monkeypatch.context() as patch:
        _stat_fails_once(patch, name, nth, root)
        seen = leftovers(root, AID)
    assert seen or [n for n in os.listdir(root) if n.startswith(AID)] == []  # empty only if gone


@pytest.mark.parametrize("nth", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("name", ["lstat", "stat"])
@pytest.mark.parametrize("shape", _SHAPES)
def test_cleanup_never_calls_it_clean_after_one_failed_stat(
    tmp_path, monkeypatch, shape, name, nth
):
    marker = _orphan(tmp_path)
    root = tmp_path / "runs"
    _lay(root, shape)
    monkeypatch.setattr(Intake, "verifier_account", lambda self: None)
    with monkeypatch.context() as patch:
        _stat_fails_once(patch, name, nth, root)
        out = module.cleanup(Intake(tmp_path))
    if out["cleaned"]:
        assert _on_disk(root) == [] and not marker.exists()
    else:
        assert out["kept"] == [AID] and marker.exists() and out["leftovers"][AID]


@pytest.mark.parametrize("nth", [1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("name", ["lstat", "stat"])
@pytest.mark.parametrize("shape", _SHAPES)
def test_recover_never_counts_zero_after_one_failed_stat(
    project,  # noqa: F811
    tmp_path,
    monkeypatch,
    shape,
    name,
    nth,  # noqa: F811
):
    crash(project, tmp_path, "WORKER_STARTED")
    attempt = own_attempt(tmp_path)
    assert attempt
    root = tmp_path / "runs"
    _lay(root, shape, attempt)
    ledger, notary, journal = stores(tmp_path)
    with monkeypatch.context() as patch:
        _stat_fails_once(patch, name, nth, root)
        (decision,) = recover(ledger=ledger, notary=notary, journal=journal, run_root=root)
    if decision.leftover_run_dirs == 0:
        assert "RECOVERY_ERROR" not in decision.detail
        assert [n for n in os.listdir(root) if n.startswith(attempt)] == []


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o400, 0o600])
def test_a_verify_log_directory_that_cannot_be_searched_names_its_run_dir(tmp_path, mode):
    marker = _orphan(tmp_path)
    root = tmp_path / "runs"
    log = root / f"{AID}-v-v0.priv"
    (log / "run").mkdir(parents=True)
    os.chmod(log, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(log, 0o700)
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"][AID] and (log / "run").exists()


@pytest.mark.parametrize("mode", [0o100, 0o300])
def test_a_searchable_verify_log_directory_is_judged_by_what_is_left(tmp_path, mode):
    _orphan(tmp_path)
    root = tmp_path / "runs"
    log = root / f"{AID}-v-v0.priv"
    (log / "run").mkdir(parents=True)
    os.chmod(log, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
        left = (log / "run").exists()
    finally:
        os.chmod(log, 0o700)
    assert bool(out["cleaned"]) is (not left)  # 0o300 removes it; 0o100 cannot and says so
    assert left == bool(out.get("leftovers", {}).get(AID))


# #1802 round 4: the marker directory and the state directory, on every Python version
@pytest.mark.parametrize("mode", [0o400, 0o600, 0o000, 0o200, 0o100])
def test_a_marker_directory_that_cannot_be_searched_is_kept_not_raised(tmp_path, mode):
    marker = _orphan(tmp_path)
    runs = marker.parent
    os.chmod(runs, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(runs, 0o700)
    assert out["cleaned"] == [] and out["kept"] and marker.exists()


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o400, 0o600])
def test_a_state_directory_that_cannot_be_searched_is_kept_not_clean(tmp_path, mode):
    marker = _orphan(tmp_path)
    intake = Intake(tmp_path)
    os.chmod(tmp_path, mode)
    try:
        out = module.cleanup(intake)
    finally:
        os.chmod(tmp_path, 0o700)
    assert out["cleaned"] == [] and out["kept"] and marker.exists()


@pytest.mark.parametrize("mode", [0o700, 0o500])
def test_a_searchable_marker_directory_is_cleaned(tmp_path, mode):
    marker = _orphan(tmp_path)
    os.chmod(marker.parent, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(marker.parent, 0o700)
    assert out == {"cleaned": [AID]} or (mode == 0o500 and out["kept"] == [AID])  # 0o500: no unlink


@pytest.mark.parametrize("state", ["absent", "empty"])
def test_no_marker_directory_or_an_empty_one_has_nothing_to_clean(tmp_path, state):
    if state == "empty":
        (tmp_path / "verify-runs").mkdir()
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": []}


@pytest.mark.parametrize("kind", ["dangling_symlink", "plain_file"])
def test_an_account_root_that_is_not_a_directory_keeps_the_attempt(tmp_path, monkeypatch, kind):
    marker = _orphan(tmp_path)
    root = tmp_path / "acct-runs"
    if kind == "plain_file":
        root.write_text("x")
    else:
        root.symlink_to(tmp_path / "nowhere")
    monkeypatch.setattr(Intake, "verifier_account", lambda self: _Account(root))
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"] == {AID: ["verifier-account-root-unreadable"]}
