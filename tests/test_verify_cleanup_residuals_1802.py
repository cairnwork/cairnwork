"""#1802: the verify cleanup does not abort on a step that cannot be done (an index path that is a
directory, a marker or the marker directory that cannot be read); it keeps the attempt, reports
leftover clones, tells the verify caller what was kept, and reads one spelling of the start mark."""

import os

import pytest
from verify_helpers import VERIFY, base_of, candidate

from cairnwork import confined, proc_start
from cairnwork import verify_candidate as module
from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused

AID = "a0123456789abcdef"
LATER = "a1123456789abcdef"

pytestmark = pytest.mark.skipif(os.getuid() == 0, reason="root reads and removes anything")


def _orphan(state, aid=AID, mark=""):
    (state / "verify-runs").mkdir(parents=True, exist_ok=True)
    marker = state / "verify-runs" / aid
    marker.write_text(mark)
    scratch = state / "verify-scratch" / aid
    scratch.mkdir(parents=True)
    (scratch / "f").write_text("x")
    return marker


def _kept(out, *names):
    assert out["cleaned"] == [] and out["kept"] == list(names)


# item 3: an index path that is a directory
def test_an_index_path_that_is_a_directory_keeps_the_attempt_and_does_not_abort(tmp_path):
    marker = _orphan(tmp_path)
    index = tmp_path / "runs" / f"{AID}.index"
    index.mkdir(parents=True)
    (index / "inner").write_text("x")
    _kept(module.cleanup(Intake(tmp_path)), AID)
    assert marker.exists()


def test_a_regular_index_file_is_removed_and_the_attempt_cleaned(tmp_path):
    marker = _orphan(tmp_path)
    index = tmp_path / "runs" / f"{AID}.index"
    index.parent.mkdir(parents=True)
    index.write_text("x")
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists() and not index.exists()


# item 3: an unreadable marker
def test_an_unreadable_marker_is_kept_and_the_next_marker_is_still_cleaned(tmp_path):
    first = _orphan(tmp_path)
    _orphan(tmp_path, LATER)
    os.chmod(first, 0o000)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(first, 0o600)
    assert out == {"cleaned": [LATER], "kept": [AID]}
    assert first.exists()


def test_a_readable_marker_is_cleaned(tmp_path):
    marker = _orphan(tmp_path)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


# item 3: an unreadable verify-runs directory
def test_an_unreadable_marker_directory_is_reported_and_does_not_abort(tmp_path):
    _orphan(tmp_path)
    os.chmod(tmp_path / "verify-runs", 0o000)
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(tmp_path / "verify-runs", 0o700)
    assert out == {"cleaned": [], "kept": ["verify-runs"]}


def test_a_state_without_markers_reports_nothing_kept(tmp_path):
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": []}


# item 4: leftover clones
def _clone(tmp_path):
    clone = tmp_path / "runs" / AID
    (clone / "sub").mkdir(parents=True)
    (clone / "sub" / "f").write_text("x")
    return clone


def test_a_leftover_clone_that_cannot_go_is_reported_and_keeps_the_marker(tmp_path):
    marker = _orphan(tmp_path)
    clone = _clone(tmp_path)
    os.chmod(clone.parent, 0o500)  # nothing can be removed from the run root
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(clone.parent, 0o700)
    _kept(out, AID)
    assert out["leftovers"] == {AID: [AID]}
    assert marker.exists() and clone.exists()


def test_a_leftover_clone_that_goes_is_removed_and_not_reported(tmp_path):
    marker = _orphan(tmp_path)
    clone = _clone(tmp_path)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists() and not clone.exists()


# item 5: the verify run shows the caller what cleanup kept
def _refused_run(state, project):
    base = base_of(project)
    cand = candidate(project, base, {"src/other.py": "x = 1\n"})  # outside the allowed files
    return module.verify_candidate(
        Intake(state), repo=project, base=base, candidate=cand,
        allowed_files=("src/calc.py",), verifiers=(("v", tuple(VERIFY.split())),),
    )  # fmt: skip


def test_a_verify_run_names_the_attempts_its_cleanup_kept(project, state, monkeypatch):
    marker = _orphan(state)
    monkeypatch.setattr(module, "sweep_marked", lambda *a, **k: confined.UNLISTED)
    out = _refused_run(state, project)
    assert out["status"] == "REFUSED"
    assert out["cleanup_kept"] == [AID]
    assert marker.exists()


def test_a_verify_run_with_nothing_kept_says_so(project, state):
    out = _refused_run(state, project)
    assert out["cleanup_kept"] == []


# item 2: one canonical spelling of the start mark
def _hidden_old_process(monkeypatch):
    monkeypatch.setattr(proc_start, "LINUX", True)
    monkeypatch.setattr(proc_start, "_boot_id", lambda proc: "boot1")
    monkeypatch.setattr(proc_start, "boot_start", lambda pid, proc="/proc": 10.0)
    monkeypatch.setattr(proc_start, "boot_mark", lambda proc="/proc": "boot1 5000.0")
    monkeypatch.setattr(confined, "_restricted", lambda: "")
    monkeypatch.setattr(confined, "_reread", lambda pids: None)
    row = (999999, 999999, os.getuid(), None)
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: [row])


@pytest.mark.parametrize(
    "mark",
    ["boot1 1_000.0", "boot1 1e3", "boot1 +1000.0", "boot1 １０００.０", "boot1 1000.0\n"]
    + ["boot1 1000.0 ", "boot1 .5", "boot1 1000.", "boot1 0x10", "boot1 1,000.0"],
)
def test_a_start_mark_in_any_other_spelling_keeps_the_marker(tmp_path, monkeypatch, mark):
    marker = _orphan(tmp_path, mark=mark)
    _hidden_old_process(monkeypatch)
    _kept(module.cleanup(Intake(tmp_path)), AID)
    assert marker.exists()


@pytest.mark.parametrize(
    "mark", ["boot1 1000.0", "boot1 1000", "boot1 4999", "boot1 4999.987654321"]
)
def test_a_canonical_start_mark_is_trusted(tmp_path, monkeypatch, mark):
    marker = _orphan(tmp_path, mark=mark)
    _hidden_old_process(monkeypatch)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists()


# the verifier account's own run root: clones made by the account are looked for there too
class _Account:
    def __init__(self, run_root):
        self.run_root = run_root
        self.removed = []

    def remove(self, path):
        import shutil

        self.removed.append(path)
        shutil.rmtree(path, ignore_errors=True)


def _with_account(monkeypatch, account):
    monkeypatch.setattr(Intake, "verifier_account", lambda self: account)


def test_an_account_side_clone_that_cannot_be_removed_keeps_the_attempt(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)
    runs = tmp_path / "acct-runs"
    clone = runs / f"{AID}-v-v0"
    (clone / "inner").mkdir(parents=True)
    os.chmod(runs, 0o500)  # nothing under it can be deleted
    _with_account(monkeypatch, _Account(runs))
    try:
        out = module.cleanup(Intake(tmp_path))
    finally:
        os.chmod(runs, 0o700)
    assert out["cleaned"] == [] and out["kept"] == [AID]
    assert out["leftovers"] == {AID: [f"{AID}-v-v0"]}
    assert marker.exists()


def test_an_account_side_clone_that_is_removed_lets_the_attempt_be_cleaned(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)
    runs = tmp_path / "acct-runs"
    (runs / f"{AID}-v-v0" / "inner").mkdir(parents=True)
    (runs / f"{AID}-v-v0.work").mkdir()
    account = _Account(runs)
    _with_account(monkeypatch, account)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not marker.exists() and not (runs / f"{AID}-v-v0").exists() and account.removed


def test_an_account_root_that_cannot_be_checked_keeps_the_attempt(tmp_path, monkeypatch):
    marker = _orphan(tmp_path)

    def refused(self):
        raise IntakeRefused("BAD_VERIFIER_ACCOUNT", "untrusted verifier root")

    monkeypatch.setattr(Intake, "verifier_account", refused)
    out = module.cleanup(Intake(tmp_path))
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert AID in out["leftovers"]


def test_no_verifier_account_leaves_the_owner_side_result_unchanged(tmp_path, monkeypatch):
    _orphan(tmp_path)
    _with_account(monkeypatch, None)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}


# a run root that exists but cannot be searched is "cannot see", not "nothing left"
def _clone_in(root, aid=AID):
    (root / aid / "tests").mkdir(parents=True)
    (root / aid / "tests" / "hidden_test.py").write_text("x")
    (root / f"{aid}-v-v0").mkdir()


@pytest.mark.parametrize("mode", [0o000, 0o200, 0o400, 0o600])
@pytest.mark.parametrize("what", ["clone", "verifier_clone", "index", "tests_index"])
def test_an_unsearchable_run_root_keeps_the_attempt(tmp_path, mode, what):
    marker = _orphan(tmp_path)
    root = tmp_path / "runs"
    root.mkdir()
    if what == "clone":
        _clone_in(root)
    elif what == "verifier_clone":
        (root / f"{AID}-v-v0").mkdir()
    else:
        (root / f"{AID}{'.index' if what == 'index' else '.tests.index'}").write_text("x")
    os.chmod(root, mode)
    try:
        out = module.cleanup(Intake(tmp_path))
        from cairnwork.recovery import UNSEARCHABLE, _leftover_names, _remove_leftovers

        _remove_leftovers(root, AID)
        seen = _leftover_names(root, AID)
    finally:
        os.chmod(root, 0o700)
    assert out["cleaned"] == [] and out["kept"] == [AID] and marker.exists()
    assert out["leftovers"] == {AID: [UNSEARCHABLE]}
    assert seen == [UNSEARCHABLE]  # what recover() counts: never a silent 0


def test_a_run_root_that_is_absent_empty_or_read_only_and_empty_is_cleaned(tmp_path):
    for mode in (None, 0o700, 0o500):
        _orphan(tmp_path)
        root = tmp_path / "runs"
        if mode is not None:
            root.mkdir(exist_ok=True)
            os.chmod(root, mode)
        try:
            assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
        finally:
            if mode is not None:
                os.chmod(root, 0o700)


def test_a_verify_log_directory_kept_as_evidence_does_not_block_cleaning(tmp_path):
    _orphan(tmp_path)
    log = tmp_path / "runs" / f"{AID}-v-v0.priv"
    log.mkdir(parents=True)
    (log / "verifier.log").write_text("log")
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert (log / "verifier.log").exists()


def test_a_removable_clone_is_removed_and_the_attempt_cleaned(tmp_path):
    _orphan(tmp_path)
    root = tmp_path / "runs"
    _clone_in(root)
    assert module.cleanup(Intake(tmp_path)) == {"cleaned": [AID]}
    assert not (root / AID).exists() and not (root / f"{AID}-v-v0").exists()
