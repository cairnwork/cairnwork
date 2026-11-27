"""Focused regression tests for deferred test overlay safeguards."""

from pathlib import Path
from typing import Any

import pytest

import cairnwork.deferred_tests as dt
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.deferred_tests import lay_tests_over


def test_existing_overlay_index_is_refused_without_running_git(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    run_root.mkdir()
    attempt_id = "a0123456789abcdef"
    index = run_root / f"{attempt_id}.tests.index"
    index.write_text("occupied")

    def unexpected_overlay(*args: object, **kwargs: object) -> str:
        pytest.fail("overlay must not run when the private index already exists")

    monkeypatch.setattr("cairnwork.deferred_tests.overlay_tree", unexpected_overlay)

    with pytest.raises(Rejected) as caught:
        lay_tests_over(
            Path("repo"), "candidate", "tests", ("tests/test_x.py",), attempt_id, run_root
        )

    assert caught.value.code == "TEST_OVERLAY_FAILED"
    assert index.read_text() == "occupied"


def test_nested_test_root_checks_each_base_component(monkeypatch):
    calls: list[tuple[object, ...]] = []

    def git(_repo: Path, *args: object, **_kwargs: object) -> str:
        calls.append(args)
        return "040000 tree abc\ttests\0" if len(calls) == 1 else "100644 blob def\ttests/unit\0"

    monkeypatch.setattr(dt, "run_git", git)
    with pytest.raises(Rejected) as caught:
        dt.check_roots_on_base(Path("repo"), "base", ("tests/unit",))

    assert caught.value.code == "TEST_ROOT_NOT_A_DIRECTORY"
    assert len(calls) == 2
    assert calls[1][-2:] == ("--", "tests/")


def test_tree_distinguishes_missing_blob_from_a_present_unreadable_blob(repo, monkeypatch):
    original = dt.run_git

    def failed_read(_repo: Path, *args: Any, **kwargs: Any) -> str:
        if args[:2] == ("show", "base:tests/missing.py"):
            raise dt.GitCommandError("missing")
        if args[:2] == ("ls-tree", "--name-only"):
            return ""
        return original(_repo, *args, **kwargs)

    monkeypatch.setattr(dt, "run_git", failed_read)
    assert dt._tree(repo, "base:tests/missing.py") is None
    with pytest.raises(Rejected) as caught:
        dt._tree(repo, "base:tests/missing.py", present=True)
    assert caught.value.code == "TEST_COMMIT_UNREADABLE"


def test_existing_overlay_index_symlink_is_also_refused(tmp_path, monkeypatch):
    run_root = tmp_path / "runs"
    run_root.mkdir()
    attempt_id = "a1123456789abcdef"
    target = tmp_path / "occupied"
    target.write_text("keep")
    index = run_root / f"{attempt_id}.tests.index"
    index.symlink_to(target)

    def unexpected_overlay(*args: object, **kwargs: object) -> str:
        pytest.fail("overlay must not run when its private index is a symlink")

    monkeypatch.setattr(dt, "overlay_tree", unexpected_overlay)
    with pytest.raises(Rejected) as caught:
        lay_tests_over(Path("repo"), "candidate", "tests", (), attempt_id, run_root)

    assert caught.value.code == "TEST_OVERLAY_FAILED"
    assert index.is_symlink() and target.read_text() == "keep"


def test_overlay_failure_is_rejected_and_private_index_cleanup_is_best_effort(
    tmp_path, monkeypatch
):
    run_root = tmp_path / "runs"
    run_root.mkdir()

    def failed_overlay(*args: object, **kwargs: object) -> str:
        raise ValueError("tree failure")

    monkeypatch.setattr(dt, "overlay_tree", failed_overlay)
    with pytest.raises(Rejected) as caught:
        lay_tests_over(Path("repo"), "candidate", "tests", (), "a2123456789abcdef", run_root)

    assert caught.value.code == "TEST_OVERLAY_FAILED"
    assert not list(run_root.iterdir())
