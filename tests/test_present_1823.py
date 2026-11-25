"""#1823: "is it there?" tests on owner state and the walkers: only "not found" means absent.

Python 3.14's pathlib exists()/is_dir()/is_file() swallow every OSError (3.13 raises), so the
same unreadable path read as absent on one interpreter and not on the other. The tests make the
lookup fail with PermissionError (monkeypatch, so root and the interpreter do not matter).
"""

import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest

from cairnwork import bytecode_walk, deferred_store, issue_runner, present
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.deferred_store import DeferredStore
from cairnwork.intake_config import has_token, stored_config
from cairnwork.intake_request import IntakeRefused, check_requests
from cairnwork.runtime_reach import check_runtime_reach, resolve_record

SHA = "a" * 40


@pytest.fixture
def deny(monkeypatch):
    """Make stat and lstat of any path named ``name`` fail with PermissionError."""

    def install(name: str) -> None:
        for attr in ("stat", "lstat"):
            real = getattr(os, attr)

            def guarded(path, *args, _real=real, **kwargs):
                if os.path.basename(os.fspath(path)) == name:
                    raise PermissionError(13, "Permission denied")
                return _real(path, *args, **kwargs)

            monkeypatch.setattr(os, attr, guarded)

    return install


# the shared helper
def test_present_raises_on_an_unreadable_path(tmp_path, deny):
    (tmp_path / "x").write_text("1")
    deny("x")
    for probe in (present.exists, present.is_dir, present.is_file):
        with pytest.raises(PermissionError):
            probe(tmp_path / "x")


def test_present_says_no_only_for_not_found_and_not_a_directory(tmp_path):
    (tmp_path / "f").write_text("1")
    for path in (tmp_path / "nope", tmp_path / "f" / "below"):
        assert not present.exists(path)
        assert not present.is_dir(path)
        assert not present.is_file(path)


def test_present_reads_files_dirs_and_links_as_pathlib_does(tmp_path):
    (tmp_path / "f").write_text("1")
    (tmp_path / "d").mkdir()
    (tmp_path / "ln").symlink_to("f")
    (tmp_path / "dangling").symlink_to("nope")
    assert present.is_file(tmp_path / "f") and present.is_dir(tmp_path / "d")
    assert present.is_file(tmp_path / "ln") and not present.is_file(tmp_path / "ln", follow=False)
    assert not present.exists(tmp_path / "dangling")
    assert present.exists(tmp_path / "dangling", follow=False)


# intake_config (stored config, token file)
def test_stored_config_raises_when_the_path_cannot_be_looked_up(tmp_path, deny):
    (tmp_path / "config.json").write_text("{}")
    deny("config.json")
    with pytest.raises(PermissionError):
        stored_config(tmp_path / "config.json")


def test_stored_config_of_a_missing_file_is_empty_and_a_readable_one_is_read(tmp_path):
    assert stored_config(tmp_path / "config.json") == {}
    (tmp_path / "config.json").write_text('{"a": 1}')
    assert stored_config(tmp_path / "config.json") == {"a": 1}


def test_has_token_is_false_unreadable_or_missing_and_true_when_readable(tmp_path, deny):
    token = tmp_path / "token"
    assert not has_token(token)
    token.write_text("secret\n")
    assert has_token(token)
    deny("token")
    assert not has_token(token)


# deferred_store (test commit already exposed; bundle present)
def test_an_unreadable_exposure_mark_is_not_read_as_unexposed(tmp_path, monkeypatch, deny):
    used = tmp_path / "used"
    used.mkdir()
    (used / SHA).write_text("")
    monkeypatch.setattr(deferred_store, "commit_present", lambda repo, sha: False)
    deny(SHA)
    with pytest.raises(PermissionError):
        DeferredStore._reject_known(tmp_path, SHA, used)


def test_a_commit_with_no_mark_and_not_in_the_mirror_is_not_exposed(tmp_path, monkeypatch):
    used = tmp_path / "used"
    used.mkdir()
    monkeypatch.setattr(deferred_store, "commit_present", lambda repo, sha: False)
    DeferredStore._reject_known(tmp_path, SHA, used)  # no raise


def test_a_burned_commit_is_still_rejected_and_an_unreadable_bundle_is_not_absent(tmp_path, deny):
    used = tmp_path / "used"
    used.mkdir()
    (used / SHA).write_text("")
    with pytest.raises(TestCommitRejected) as caught:
        DeferredStore._reject_known(tmp_path, SHA, used)
    assert caught.value.code == "TEST_COMMIT_ALREADY_EXPOSED"
    store = DeferredStore(tmp_path / "store")
    store.path.mkdir()
    (store.path / f"{SHA}.bundle").write_text("")
    assert store.has(SHA)
    deny(f"{SHA}.bundle")
    with pytest.raises(PermissionError):
        store.has(SHA)


# intake_request (check_requests)
def _ledger(state: Path, *, bound: bool) -> None:
    with sqlite3.connect(state / "ownership.sqlite") as conn:
        conn.execute("CREATE TABLE task_ownership (task_id TEXT)")
        if bound:
            conn.execute("INSERT INTO task_ownership VALUES ('t')")
    conn.close()


def test_an_unlookable_ledger_is_refused_not_read_as_no_ledger(tmp_path, deny):
    _ledger(tmp_path, bound=True)
    deny("ownership.sqlite")
    with pytest.raises(IntakeRefused) as caught:
        check_requests(tmp_path)
    assert caught.value.code == "BAD_STATE"


def test_no_ledger_and_no_requests_dir_is_fine_and_a_bound_one_is_refused(tmp_path):
    check_requests(tmp_path)
    _ledger(tmp_path, bound=True)
    with pytest.raises(IntakeRefused):
        check_requests(tmp_path)


def test_a_plain_requests_dir_passes_and_a_symlinked_one_does_not(tmp_path):
    _ledger(tmp_path, bound=True)
    (tmp_path / "requests").mkdir()
    check_requests(tmp_path)
    (tmp_path / "requests").rmdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "requests").symlink_to("elsewhere")
    with pytest.raises(IntakeRefused):
        check_requests(tmp_path)


# issue_runner (_recall of a known task)
def _recall(path: Path, monkeypatch) -> Any:
    monkeypatch.setattr(issue_runner, "_record_path", lambda intake, repo, number: path)
    return issue_runner._recall(None, "o/r", 1)  # type: ignore[arg-type]


def test_an_unlookable_task_record_is_not_read_as_no_record(tmp_path, monkeypatch, deny):
    (tmp_path / "rec.json").write_text('{"task_id": "t1"}')
    deny("rec.json")
    with pytest.raises(PermissionError):
        _recall(tmp_path / "rec.json", monkeypatch)


def test_a_missing_task_record_is_none_and_a_readable_one_is_returned(tmp_path, monkeypatch):
    assert _recall(tmp_path / "rec.json", monkeypatch) is None
    (tmp_path / "rec.json").write_text('{"task_id": "t1"}')
    assert _recall(tmp_path / "rec.json", monkeypatch) == {"task_id": "t1"}


def test_a_bad_task_record_is_still_issue_record_unreadable(tmp_path, monkeypatch):
    (tmp_path / "rec.json").write_text("[1]")
    with pytest.raises(ValueError, match="ISSUE_RECORD_UNREADABLE"):
        _recall(tmp_path / "rec.json", monkeypatch)


# bytecode_walk (a file this user cannot open)
def _listed(directory: Path) -> dict[str, tuple[str, Any]]:
    _, found = bytecode_walk.opened(str(directory))
    return {name: (kind, error) for name, kind, _, error in found}


def test_an_unreadable_file_is_an_error_row_not_not_bytecode(tmp_path, monkeypatch):
    (tmp_path / "blob").write_bytes(b"data")
    real = os.open

    def opener(path, flags, *args, **kwargs):
        if path == "blob" and "dir_fd" in kwargs:
            raise PermissionError(13, "Permission denied")
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", opener)
    kind, error = _listed(tmp_path)["blob"]
    assert error == "Permission denied"


def test_a_readable_file_that_is_not_bytecode_stays_clean(tmp_path):
    (tmp_path / "blob").write_bytes(b"data")
    assert _listed(tmp_path)["blob"] == ("other", None)


def test_a_readable_file_starting_as_bytecode_is_bytecode(tmp_path):
    (tmp_path / "blob").write_bytes(bytecode_walk.MAGIC + b"x")
    assert _listed(tmp_path)["blob"] == ("bytecode", None)
    assert stat.S_ISREG(os.stat(tmp_path / "blob").st_mode)


# runtime_reach (resolve_record, _rels)
def _checkout(tmp_path: Path) -> Path:
    root = (tmp_path / "checkout").resolve()
    (root / "plug").mkdir(parents=True)
    (root / "plug" / "real.py").write_text("")
    return root


def _record(root: Path, entry: str) -> CollectedRecord:
    return CollectedRecord(frozenset(), (), (entry,), (), str(root))


def test_an_unresolvable_entry_refuses_at_resolve_and_at_check_time(tmp_path, deny):
    root = _checkout(tmp_path)
    entry = str(root / "plug" / "sub" / "real.py")
    (root / "plug" / "sub").mkdir()
    deny("sub")
    with pytest.raises(TestCommitRejected):
        resolve_record(_record(root, entry), root)
    with pytest.raises(TestCommitRejected):
        check_runtime_reach(_record(root, entry), ["plug/sub/real.py"])


def test_a_missing_entry_is_absent_and_keeps_its_lexical_path(tmp_path):
    root = _checkout(tmp_path)
    entry = str(root / "plug" / "gone.zip")
    resolved = resolve_record(_record(root, entry), root)
    assert dict(resolved.real)[entry] == entry
    check_runtime_reach(resolved, ["plug/real.py"])  # unrelated change: no refusal
    with pytest.raises(TestCommitRejected):
        check_runtime_reach(resolved, ["plug/gone.zip"])  # the commit supplies the entry


def test_a_readable_symlinked_entry_still_resolves_to_its_target(tmp_path):
    root = _checkout(tmp_path)
    (root / "plug" / "alias.py").symlink_to("real.py")
    entry = str(root / "plug" / "alias.py")
    resolved = resolve_record(_record(root, entry), root)
    assert dict(resolved.real)[entry] == str(root / "plug" / "real.py")
