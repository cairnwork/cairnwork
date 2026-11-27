"""#1823 family sweep, second part: an unreadable path is raised, never read as absent.

Python 3.14's pathlib exists()/is_file() swallow every OSError (3.13 raises). The sites here read
"cannot look" as "not there": the signed statement's journal lookup, the bundle-exists check, the
grafts check and the snapshot's index check. The lookup is made to fail with PermissionError
(monkeypatch), so root and the interpreter version do not matter.
"""

import os
import sys
from pathlib import Path

import pytest
from conftest import git

from cairnwork import lineage
from cairnwork.snapshot import snapshot_candidate
from cairnwork.workspace import CandidateRejected, create_attempt_clone
from cairnwork_attest import on_verified
from cairnwork_attest.statement import run_test_source


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


# the signed statement's journal lookup
def test_an_unreadable_journal_is_raised_not_none(tmp_path, deny):
    (tmp_path / "journal.sqlite").write_text("x")
    deny("journal.sqlite")
    with pytest.raises(PermissionError):
        run_test_source(tmp_path, "task-1", "a1")


def test_a_missing_journal_is_still_none(tmp_path):
    assert run_test_source(tmp_path, "task-1", "a1") == "none"
    assert run_test_source(tmp_path / "gone" / "below", "task-1", "a1") == "none"


# the bundle-exists check
def test_an_unreadable_bundle_path_is_raised_not_absent(tmp_path, deny):
    (tmp_path / "b.json").write_text("{}")
    deny("b.json")
    with pytest.raises(PermissionError):
        on_verified._is_there(tmp_path / "b.json")


def test_the_bundle_check_says_no_only_for_not_found(tmp_path):
    (tmp_path / "f").write_text("1")
    assert on_verified._is_there(tmp_path / "f")
    assert not on_verified._is_there(tmp_path / "nope")
    assert not on_verified._is_there(tmp_path / "f" / "below")


# the grafts check
def test_an_unreadable_grafts_path_is_raised_not_absent(tmp_path, deny, monkeypatch):
    monkeypatch.setattr(lineage, "run_git", lambda *a, **k: "info/grafts\n")
    (tmp_path / "info").mkdir()
    (tmp_path / "info" / "grafts").write_text("")
    deny("grafts")
    with pytest.raises(PermissionError):
        lineage.refuse_grafts(tmp_path)


def test_no_grafts_file_passes_and_a_present_one_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(lineage, "run_git", lambda *a, **k: "info/grafts\n")
    lineage.refuse_grafts(tmp_path)
    (tmp_path / "info").mkdir()
    (tmp_path / "info" / "grafts").write_text("")
    with pytest.raises(RuntimeError, match="LINEAGE_GRAFTS_REFUSED"):
        lineage.refuse_grafts(tmp_path)


# the snapshot's index check
@pytest.fixture
def attempt(repo, tmp_path):
    return create_attempt_clone(repo, git(repo, "rev-parse", "HEAD"), tmp_path / "att", "a1")


def test_an_unreadable_index_path_is_raised_not_absent(repo, attempt, deny):
    deny("a1.index")
    with pytest.raises(PermissionError):
        snapshot_candidate(repo, attempt, ("src/a.py",), python=sys.executable)


def test_a_dangling_index_link_is_refused_and_a_free_name_is_not(repo, attempt):
    index = Path(attempt.path.parent) / "a1.index"
    index.symlink_to(attempt.path.parent / "nowhere")
    with pytest.raises(CandidateRejected) as info:
        snapshot_candidate(repo, attempt, ("src/a.py",), python=sys.executable)
    assert info.value.code == "UNREADABLE_WORKTREE"
    index.unlink()
    with pytest.raises(CandidateRejected) as info:  # no change: refused for that, not the index
        snapshot_candidate(repo, attempt, ("src/a.py",), python=sys.executable)
    assert info.value.code != "UNREADABLE_WORKTREE"


def test_an_unreadable_bundle_stops_the_entry_run_at_the_statement_step(
    tmp_path, deny, monkeypatch
):
    """Entry level (#1823): the failure travels up through _run_steps; nothing is written or
    published, and the runner's step list ends with an ERROR statement step."""
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(on_verified, "verify_record", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(on_verified, "load_private_key", lambda *a, **k: key)
    bundle = tmp_path / "unreadable-bundle.json"
    deny(bundle.name)
    args = SimpleNamespace(state=str(tmp_path), task_id="t", attempt_id="a", candidate="c")
    pubkey = SimpleNamespace(public=None, path=tmp_path / "pub.pem")
    steps = on_verified._run_steps(args, bundle, pubkey)  # type: ignore[arg-type]
    assert [(s.step, s.code) for s in steps] == [("statement", "ERROR")]
    assert not os.path.lexists(bundle)
