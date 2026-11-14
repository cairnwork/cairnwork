"""Candidate supplied text stays a single quoted token in refusals."""

import errno
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest  # pyright: ignore[reportMissingImports]


class E1:
    def __str__(self):
        return "bad\n::error::first\rsecond\r\nthird"


class E2:
    def __str__(self):
        return "bad\u2028middle\u2029end"


class E3:
    def __str__(self):
        return "bad\u0085end"


class E4:
    def __str__(self):
        return "bad\\tail"


class E5:
    def __str__(self):
        return "bad'quote"


class E6:
    def __str__(self):
        return "bad\U000f0000"


def _attempt(path=None) -> Any:
    return SimpleNamespace(base_commit="a" * 40, path=Path(path or "/tmp/clone"))


def _entry(path, change_type="MODIFY", mode="100644"):
    return SimpleNamespace(
        path=path, change_type=change_type, before_mode="100644", after_mode=mode
    )


def _workspace_refusal(monkeypatch, site, tmp_path, hostile_value: Any = None):
    import cairnwork.workspace as workspace
    from cairnwork.changeset import ChangeSetError
    from cairnwork.gitcmd import GitCommandError
    from cairnwork.workspace import CandidateRejected

    hostile = str(hostile_value if hostile_value is not None else E1())
    attempt = _attempt(tmp_path / "clone")
    entry = _entry("allowed.py")
    change = SimpleNamespace(entries=(entry,), paths=[hostile])
    monkeypatch.setattr(workspace, "_parents", lambda *a: [attempt.base_commit])
    monkeypatch.setattr(workspace, "read_change_set", lambda *a: change)
    if site == "01":
        monkeypatch.setattr(
            workspace, "read_change_set", lambda *a: (_ for _ in ()).throw(ChangeSetError(hostile))
        )
    elif site == "02":
        entry.path = hostile
    elif site == "03":
        entry.change_type = "DELETE"
        entry.path = hostile
    elif site == "04":
        entry.after_mode = "120000"
        entry.path = hostile
    elif site == "05":
        monkeypatch.setattr(
            workspace, "scan_patch", lambda *a, **k: (_ for _ in ()).throw(GitCommandError(hostile))
        )
    elif site == "06":
        monkeypatch.setattr(workspace, "scan_patch", lambda *a, **k: ())
        monkeypatch.setattr(
            workspace,
            "run_git_bytes",
            lambda *a, **k: (_ for _ in ()).throw(GitCommandError(hostile)),
        )
        monkeypatch.setattr(workspace, "diff_check_detail", lambda *a: hostile)
    elif site == "07":
        monkeypatch.setattr(workspace, "scan_patch", lambda *a, **k: ())
        monkeypatch.setattr(workspace, "run_git_bytes", lambda *a, **k: b"")
        monkeypatch.setattr(
            workspace,
            "tree_differences",
            lambda *a: (_ for _ in ()).throw(GitCommandError(hostile)),
        )
    elif site == "08":
        monkeypatch.setattr(workspace, "scan_patch", lambda *a, **k: ())
        monkeypatch.setattr(workspace, "run_git_bytes", lambda *a, **k: b"")
        monkeypatch.setattr(workspace, "tree_differences", lambda *a: [hostile])
    with pytest.raises(CandidateRejected) as caught:
        workspace.check_candidate_commit(
            Path("repo"),
            attempt,
            "b" * 40,
            (hostile,) if site in {"03", "04"} else ("allowed.py",),
            python="python",
        )
    return caught.value


def _snapshot_refusal(monkeypatch, site, tmp_path):
    import cairnwork.snapshot as snapshot
    from cairnwork.gitcmd import GitCommandError
    from cairnwork.workspace import CandidateRejected

    hostile = str(E1())
    clone = tmp_path / "clone"
    clone.mkdir(exist_ok=True)
    if site == "09":
        monkeypatch.setattr(snapshot, "require_refs_unchanged", lambda *a: None)
        attempt: Any = SimpleNamespace(
            attempt_id=hostile,
            base_commit="a" * 40,
            path=clone,
        )
        (tmp_path / (hostile + ".index")).write_text("")
        with pytest.raises(CandidateRejected) as caught:
            snapshot.snapshot_candidate(Path("repo"), attempt, (), python="python")
        return caught.value
    if site == "10":
        monkeypatch.setattr(snapshot, "require_refs_unchanged", lambda *a: None)
        monkeypatch.setattr(
            snapshot, "run_git", lambda *a, **k: (_ for _ in ()).throw(GitCommandError(hostile))
        )
        attempt: Any = SimpleNamespace(attempt_id="a1", base_commit="a" * 40, path=clone)
        with pytest.raises(CandidateRejected) as caught:
            snapshot.snapshot_candidate(Path("repo"), attempt, (), python="python")
        return caught.value
    path = {
        "11": "../" + hostile,
        "12": "parent\n::error::part/file.py",
        "13": hostile,
        "14": hostile,
        "15": hostile,
    }[site]
    if site == "12":
        target = tmp_path / "target"
        target.mkdir()
        (clone / "parent\n::error::part").symlink_to(target, target_is_directory=True)
    elif site == "13":
        monkeypatch.setattr(
            os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EACCES, hostile))
        )
    elif site == "14":
        (clone / path).write_text("x")
        monkeypatch.setattr(
            os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2, st_size=1)
        )
    elif site == "15":
        (clone / path).write_text("x")

        class Grown:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return b"x" * (snapshot.MAX_FILE_BYTES + 1)

        monkeypatch.setattr(os, "fdopen", lambda *a, **k: Grown())
    with pytest.raises(CandidateRejected) as caught:
        snapshot._stage(Path("repo"), clone, path, tmp_path / "index")
    return caught.value


def _check(monkeypatch, site, tmp_path):
    return (
        _workspace_refusal(monkeypatch, site, tmp_path)
        if site in {"01", "02", "03", "04", "05", "06", "07", "08"}
        else _snapshot_refusal(monkeypatch, site, tmp_path)
    )


@pytest.mark.parametrize(
    "site,code",
    [
        ("01", "UNREADABLE_CHANGE"),
        ("02", "OUT_OF_SCOPE"),
        ("03", "DELETION_FORBIDDEN"),
        ("04", "FORBIDDEN_MODE"),
        ("05", "UNREADABLE_CHANGE"),
        ("06", "DIFF_CHECK_FAILED"),
        ("07", "UNREADABLE_CHANGE"),
        ("08", "DIRTY_TREE"),
        ("09", "UNREADABLE_WORKTREE"),
        ("10", "UNREADABLE_WORKTREE"),
        ("11", "OUT_OF_SCOPE"),
        ("12", "FORBIDDEN_MODE"),
        ("13", "FORBIDDEN_MODE"),
        ("14", "FORBIDDEN_MODE"),
        ("15", "FORBIDDEN_MODE"),
    ],
    ids=lambda value: value if value.startswith("0") else None,
)
def test_candidate_rejections_escape_each_dynamic_site(site, code, monkeypatch, tmp_path):
    caught = _check(monkeypatch, site, tmp_path)
    assert caught.code == code
    assert len(str(caught).splitlines()) == 1


@pytest.mark.parametrize("site,code", [("03", "DELETION_FORBIDDEN"), ("04", "FORBIDDEN_MODE")])
@pytest.mark.parametrize("hostile", [E1(), E2(), E3()], ids=["E1", "E2", "E3"])
def test_allowed_hostile_paths_escape_deletion_and_mode_refusals(
    site, code, hostile, monkeypatch, tmp_path
):
    caught = _workspace_refusal(monkeypatch, site, tmp_path, hostile)
    assert caught.code == code
    assert len(str(caught).splitlines()) == 1


@pytest.mark.parametrize(
    "value",
    [E1(), E2(), E3(), E4(), E5(), E6()],
    ids=["E1", "E2", "E3", "E4", "E5", "E6"],
)
def test_shown_escapes_hidden_chars_and_quotes(value):
    from cairnwork.textclean import is_hidden, shown

    result = shown(value)
    assert result.startswith("'") and result.endswith("'")
    assert not any(is_hidden(char) for char in result)
    assert "\n" not in result and "\r" not in result


@pytest.mark.parametrize(
    "value", ["plain/path", "space name", "café", "cafe\u0301", "中文", "[#-]."]
)
def test_shown_preserves_clean_text(value):
    from cairnwork.textclean import shown

    assert shown(value) == "'" + value + "'"


def test_workspace_errors_escape_the_three_checkout_failure_sites(monkeypatch, tmp_path):
    import cairnwork.workspace as workspace
    from cairnwork.gitcmd import GitCommandError
    from cairnwork.workspace import WorkspaceError, create_attempt_clone

    base = "a" * 40
    hostile = str(E1())
    with pytest.raises(WorkspaceError) as first:
        monkeypatch.setattr(
            workspace, "run_git", lambda *a, **k: (_ for _ in ()).throw(GitCommandError(hostile))
        )
        create_attempt_clone(Path("repo"), base, tmp_path / "first", "a1")
    assert first.value.code == "WORKSPACE_ERROR"
    assert len(str(first.value).splitlines()) == 1

    monkeypatch.setattr(workspace, "_restrict_to_base_history", lambda *a: None)
    monkeypatch.setattr(workspace, "_guarded_refs", lambda *a: ())
    monkeypatch.setattr(
        workspace, "run_git", lambda *a, **k: "commit" if a[1:2] == ("cat-file",) else base
    )
    monkeypatch.setattr(
        workspace, "tree_differences", lambda *a: (_ for _ in ()).throw(GitCommandError(hostile))
    )
    with pytest.raises(WorkspaceError) as second:
        create_attempt_clone(Path("repo"), base, tmp_path / "second", "a1")
    assert second.value.code == "WORKSPACE_ERROR"
    assert len(str(second.value).splitlines()) == 1

    monkeypatch.setattr(workspace, "tree_differences", lambda *a: [hostile])
    monkeypatch.setattr(workspace, "_refuse_case_collisions", lambda *a: None)
    with pytest.raises(WorkspaceError) as third:
        create_attempt_clone(Path("repo"), base, tmp_path / hostile, "a1")
    assert third.value.code == "WORKSPACE_ERROR"
    assert len(str(third.value).splitlines()) == 1


def test_commit_style_out_of_scope_path_is_quoted(monkeypatch, tmp_path):
    import cairnwork.workspace as workspace
    from cairnwork.workspace import CandidateRejected

    path = str(E1())
    entry = _entry(path)
    monkeypatch.setattr(workspace, "_parents", lambda *a: ["a" * 40])
    monkeypatch.setattr(
        workspace, "read_change_set", lambda *a: SimpleNamespace(entries=(entry,), paths=[path])
    )
    with pytest.raises(CandidateRejected) as caught:
        workspace.check_candidate_commit(
            Path("repo"), _attempt(tmp_path / "clone"), "b" * 40, (), python="python"
        )
    assert caught.value.code == "OUT_OF_SCOPE"
    assert len(str(caught.value).splitlines()) == 1
