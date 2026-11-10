"""#1089: a failed base conftest read is not evidence that the file is new."""

import pytest
from conftest import commit_change, git

import cairnwork.conftest_check as cc
from cairnwork.deferred_overlay import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError

PATH = "tests/conftest.py"


def _assert_unreadable(repo, base, head):
    with pytest.raises(Rejected) as caught:
        cc.check_conftests(repo, base, head, (PATH,))
    assert caught.value.code == "TEST_COMMIT_UNREADABLE"


def test_non_utf8_base_conftest_is_unreadable(repo):
    """The variant scan's valid Python plus one non-UTF-8 byte must not become an empty base."""
    target = repo / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"import pytest\n# variant scan byte: \xff\n")
    git(repo, "add", PATH)
    git(repo, "commit", "-q", "-m", "base conftest with non-UTF-8 byte")
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, {PATH: "import pytest\n\nVALUE = 1\n"})
    head = git(repo, "rev-parse", "HEAD")

    _assert_unreadable(repo, base, head)


def test_git_failure_reading_existing_base_conftest_is_unreadable(repo, monkeypatch):
    target = repo / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("import pytest\n")
    git(repo, "add", PATH)
    git(repo, "commit", "-q", "-m", "base conftest")
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, {PATH: "import pytest\n\nVALUE = 1\n"})
    head = git(repo, "rev-parse", "HEAD")
    original = cc.run_git

    def fail_base_read(cwd, *args, **kwargs):
        if args[:2] == ("show", f"{base}:{PATH}"):
            raise GitCommandError("forced base conftest read failure")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(cc, "run_git", fail_base_read)
    _assert_unreadable(repo, base, head)


def test_new_conftest_still_uses_empty_base(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {PATH: "import pytest\n"})

    cc.check_conftests(repo, base, head, (PATH,))


def test_base_presence_check_failure_is_unreadable(repo, monkeypatch):
    target = repo / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("import pytest\n")
    git(repo, "add", PATH)
    git(repo, "commit", "-q", "-m", "base conftest")
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {PATH: "import pytest\n\nVALUE = 1\n"})
    original = cc.run_git
    original_bytes = cc.run_git_bytes

    def fail_base_read(cwd, *args, **kwargs):
        if args[:2] == ("show", f"{base}:{PATH}"):
            raise GitCommandError("forced base conftest read failure")
        return original(cwd, *args, **kwargs)

    def fail_presence(cwd, *args, **kwargs):
        if args[:3] == ("ls-tree", "-r", "-z") and args[-1] == base:
            raise GitCommandError("forced base tree presence failure")
        return original_bytes(cwd, *args, **kwargs)

    monkeypatch.setattr(cc, "run_git", fail_base_read)
    monkeypatch.setattr(cc, "run_git_bytes", fail_presence)
    _assert_unreadable(repo, base, head)
