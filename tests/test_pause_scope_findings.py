"""Regression coverage for fail-closed write scope handling."""

import hashlib
import subprocess

import pytest

from experimental.pause.flow import _scope_ok


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("base\n")
    (root / "README.md").write_text("base\n")
    (root / ".gitignore").write_text("*.log\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _pin(root):
    return hashlib.sha256((root / ".git" / "config").read_bytes()).hexdigest()


@pytest.mark.parametrize("change", ["edit", "delete", "new", "chmod", "none", "ignored"])
def test_scope_allows_in_scope_changes(tmp_path, change):
    root = _repo(tmp_path)
    if change == "edit":
        (root / "src/a.py").write_text("edited\n")
    elif change == "delete":
        (root / "src/a.py").unlink()
    elif change == "new":
        (root / "src/new.py").write_text("new\n")
    elif change == "chmod":
        (root / "src/a.py").chmod(0o755)
    elif change == "ignored":
        (root / "src/x.log").write_text("ignored\n")
    assert _scope_ok(
        root, ["src"], _git(root, "rev-parse", "HEAD").stdout.decode().strip(), _pin(root)
    )


@pytest.mark.parametrize(
    "change", ["readme_edit", "readme_delete", "top", "ignored", "symlink", "tracked_symlink"]
)
def test_scope_refuses_out_of_scope_or_symlinks(tmp_path, change):
    root = _repo(tmp_path)
    if change == "readme_edit":
        (root / "README.md").write_text("edited\n")
    elif change == "readme_delete":
        (root / "README.md").unlink()
    elif change == "top":
        (root / "new.py").write_text("new\n")
    elif change == "ignored":
        (root / "x.log").write_text("ignored\n")
    elif change == "symlink":
        (root / "src/link").symlink_to("a.py")
    else:
        (root / "src/a.py").unlink()
        (root / "src/a.py").symlink_to("README.md")
    assert not _scope_ok(
        root, ["src"], _git(root, "rev-parse", "HEAD").stdout.decode().strip(), _pin(root)
    )


def test_raw_diff_failure_fails_closed(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "src/a.py").write_text("changed\n")
    real_run = subprocess.run

    def failing_run(args, **kwargs):
        if "--raw" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"failed")
        return real_run(args, **kwargs)

    monkeypatch.setattr("experimental.pause.scope.subprocess.run", failing_run)
    assert not _scope_ok(
        root, ["src"], _git(root, "rev-parse", "HEAD").stdout.decode().strip(), _pin(root)
    )


def test_scope_is_pinned_to_base_after_head_moves(tmp_path):
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    (root / "README.md").write_text("out of scope committed\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "out of scope")
    assert not _scope_ok(root, ["src"], base, _pin(root))


def test_scope_allows_committed_in_scope_change_from_base(tmp_path):
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    (root / "src/a.py").write_text("in scope committed\n")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-qm", "in scope")
    assert _scope_ok(root, ["src"], base, _pin(root))


def test_scope_refuses_invalid_base(tmp_path):
    assert not _scope_ok(_repo(tmp_path), ["src"], "bad")


@pytest.mark.parametrize("scope", ["src", {}, ["src", 1]])
def test_non_list_write_scope_fails_closed(scope):
    from experimental.pause.flow import _write_scope

    assert _write_scope({"write_scope": scope}) == "PAUSE_SCOPE_VIOLATION"
