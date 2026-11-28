"""#1882 same-shape sweep: an unreadable path is raised or reported, never read as absent.

Each site that used to call exists()/is_dir()/is_file() (which hide every OSError on 3.14) now
goes through cairnwork.present, or a direct stat that only FileNotFoundError and
NotADirectoryError treat as "not there". Every site gets a must-block case (PermissionError
from a patched stat, or an unreadable read) and a must-pass case (really absent, or the
ancestor is a file: still absent). The patch makes the stat fail, so root does not matter.
It also emulates the 3.14 pathlib (exists/is_dir/is_file swallow every OSError), so the
must-block cases fail on the old code on 3.11-3.13 too, not only on 3.14.
"""

import os
from pathlib import Path

import pytest
from conftest import git

from cairnwork import deferred_tests, verifier_exec
from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.intake import Intake
from cairnwork.workspace import WorkspaceError, create_attempt_clone
from cairnwork_attest import keys, on_verified
from cairnwork_attest.keys import KeyFileError
from cairnwork_attest.record import RecordNotFound, read_record


@pytest.fixture
def deny(monkeypatch):
    """Make stat and lstat of any path named ``name`` fail with PermissionError."""

    def swallowing(real):
        def check(self, *args, **kwargs):
            try:
                return real(self, *args, **kwargs)
            except OSError:  # what 3.14 pathlib does: unreadable reads as absent
                return False

        return check

    for method in ("exists", "is_dir", "is_file"):
        monkeypatch.setattr(Path, method, swallowing(getattr(Path, method)))

    def install(name: str) -> None:
        for attr in ("stat", "lstat"):
            real = getattr(os, attr)

            def guarded(path, *args, _real=real, **kwargs):
                if os.path.basename(os.fspath(path)) == name:
                    raise PermissionError(13, "Permission denied")
                return _real(path, *args, **kwargs)

            monkeypatch.setattr(os, attr, guarded)

    return install


# deferred_tests.lay_tests_over: the tests.index check
def test_an_unreadable_tests_index_is_raised_not_absent(tmp_path, deny, monkeypatch):
    monkeypatch.setattr(deferred_tests, "overlay_tree", lambda *a, **k: pytest.fail("reached"))
    deny("a1.tests.index")
    with pytest.raises(PermissionError):
        deferred_tests.lay_tests_over(tmp_path, "c", "t", (), "a1", tmp_path)


def test_a_missing_tests_index_still_proceeds(tmp_path, monkeypatch):
    def reached(*a, **k):
        raise RuntimeError("reached")

    monkeypatch.setattr(deferred_tests, "overlay_tree", reached)
    with pytest.raises(RuntimeError, match="reached"):
        deferred_tests.lay_tests_over(tmp_path, "c", "t", (), "a1", tmp_path)
    (tmp_path / "f").write_text("x")
    # the parent is a file: still absent, so the check passes; the cleanup's unlink then fails
    # with NotADirectoryError (pre-existing, not a "already exists" refusal)
    with pytest.raises((RuntimeError, NotADirectoryError)) as info:
        deferred_tests.lay_tests_over(tmp_path, "c", "t", (), "a1", tmp_path / "f")
    assert not isinstance(info.value, TestCommitRejected)


def test_an_existing_tests_index_is_still_refused(tmp_path):
    (tmp_path / "a1.tests.index").write_text("x")
    with pytest.raises(TestCommitRejected, match="already exists"):
        deferred_tests.lay_tests_over(tmp_path, "c", "t", (), "a1", tmp_path)


# workspace.create_attempt_clone: the attempt path check
def test_an_unreadable_attempt_path_is_raised_not_absent(repo, tmp_path, deny):
    deny("a1")
    with pytest.raises(PermissionError):
        create_attempt_clone(repo, git(repo, "rev-parse", "HEAD"), tmp_path / "att", "a1")


def test_a_free_attempt_path_is_cloned_and_a_file_parent_is_not_already_exists(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    assert create_attempt_clone(repo, base, tmp_path / "att", "a1").path.is_dir()
    (tmp_path / "f").write_text("x")
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "f", "a1")
    assert "already exists" not in str(info.value)


# keys.generate_keypair: the state directory check
def test_an_unreadable_state_dir_is_key_unreadable(tmp_path, deny):
    state = tmp_path / "state"
    state.mkdir()
    deny("state")
    with pytest.raises(KeyFileError) as info:
        keys.generate_keypair(state)
    assert info.value.code == "KEY_UNREADABLE"


def test_a_missing_state_dir_is_still_not_found(tmp_path):
    with pytest.raises(KeyFileError) as info:
        keys.generate_keypair(tmp_path / "nope")
    assert info.value.code == "STATE_DIR_NOT_FOUND"
    (tmp_path / "f").write_text("x")
    with pytest.raises(KeyFileError) as info:
        keys.generate_keypair(tmp_path / "f" / "below")
    assert info.value.code == "STATE_DIR_NOT_FOUND"


# record.read_record: the notary database check
def test_an_unreadable_notary_database_is_record_unreadable(tmp_path, deny):
    db = tmp_path / "notary.sqlite"
    db.write_text("x")
    deny("notary.sqlite")
    with pytest.raises(RecordNotFound) as info:
        read_record(db, "t", "a")
    assert info.value.code == "RECORD_UNREADABLE"


def test_a_missing_notary_database_is_still_record_not_found(tmp_path):
    with pytest.raises(RecordNotFound) as info:
        read_record(tmp_path / "notary.sqlite", "t", "a")
    assert info.value.code == "RECORD_NOT_FOUND"
    (tmp_path / "f").write_text("x")
    with pytest.raises(RecordNotFound) as info:
        read_record(tmp_path / "f" / "notary.sqlite", "t", "a")
    assert info.value.code == "RECORD_NOT_FOUND"


# Intake.accepting: the ACCEPTING flag
def test_an_unreadable_accepting_flag_is_raised_not_off(tmp_path, deny):
    (tmp_path / "ACCEPTING").write_text("")
    deny("ACCEPTING")
    with pytest.raises(PermissionError):
        Intake(state=tmp_path).accepting()


def test_a_missing_accepting_flag_is_still_off(tmp_path):
    assert Intake(state=tmp_path).accepting() is False
    (tmp_path / "f").write_text("x")
    assert Intake(state=tmp_path / "f").accepting() is False  # the state is a file: absent
    (tmp_path / "ACCEPTING").write_text("")
    assert Intake(state=tmp_path).accepting() is True


# verifier_exec._canary: the canary log
@pytest.fixture
def no_run(monkeypatch):
    monkeypatch.setattr(verifier_exec, "_execute", lambda *a, **k: None)


def _canary(log):
    return verifier_exec._canary("py", None, None, {}, None, None, ["x"], log)


def test_an_unreadable_canary_log_is_raised_not_empty(tmp_path, deny, no_run, monkeypatch):
    log = tmp_path / "canary.log"
    log.write_text("secret output")
    deny("canary.log")
    real = Path.read_text

    def unreadable(self, *a, **k):
        if self.name == "canary.log":
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(PermissionError):
        _canary(log)


def test_a_missing_canary_log_is_still_empty(tmp_path, no_run):
    assert _canary(tmp_path / "canary.log") == ""
    (tmp_path / "f").write_text("x")
    assert _canary(tmp_path / "f" / "canary.log") == ""  # the parent is a file: absent


# #1842 on_verified._is_there (bundle exists check): a symlinked directory counts as itself
def test_a_bundle_under_a_symlinked_readable_dir_is_found_like_a_plain_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "b.json").write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert on_verified._is_there(link / "b.json") == on_verified._is_there(real / "b.json")
    assert on_verified._is_there(link / "b.json") is True
    assert on_verified._is_there(link / "nope") is False


def test_a_bundle_whose_link_target_is_unsearchable_is_refused_not_absent(tmp_path, deny):
    real = tmp_path / "real"
    real.mkdir()
    (real / "b.json").write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    deny("b.json")  # stands for an unsearchable target: the stat itself fails
    with pytest.raises(PermissionError):
        on_verified._is_there(link / "b.json")
