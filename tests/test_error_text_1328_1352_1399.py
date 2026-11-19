"""#1328 treecompare reports an undecodable path; #1352 Finding text is escaped; #1399 run_child."""

import subprocess
import sys

import pytest

from cairnwork.child_run import run_child
from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan import Finding
from cairnwork.treecompare import tree_differences


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_non_utf8_path_in_commit_is_a_difference_not_an_exception(tmp_path):
    repo, clone = tmp_path / "repo", tmp_path / "clone"
    repo.mkdir()
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"x", check=True, capture_output=True,
    ).stdout.decode().strip()  # fmt: skip
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "--index-info"],
        input=f"100644 {blob}\tbad".encode() + b"\xff\n", check=True, capture_output=True,
    )  # fmt: skip
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    assert tree_differences(repo, _git(repo, "rev-parse", "HEAD"), clone) != []


def test_missing_commit_still_raises(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(GitCommandError):
        tree_differences(repo, "0" * 40, tmp_path)


@pytest.mark.parametrize(
    ("path", "shown_path"),
    [
        ("a/b.py", "a/b.py"),
        ("a\nb.py", "a\\x0ab.py"),
        ("a‮b.py", "a\\u202eb.py"),
        ("::error::x\x1b[2K.py", "::error::x\\x1b[2K.py"),
        ("a\rb", "a\\x0db"),
        ("a\u2028b", "a\\u2028b"),
        ("a\x00b", "a\\x00b"),
        ("it's", "it\\'s"),
        ("a\\b", "a\\\\b"),
        ("bad\udcff", "bad\\udcff"),
        ("x" * 5000, "x" * 5000),
    ],
)
def test_finding_text_escapes_hidden_characters(path, shown_path):
    assert str(Finding(path, 3, "rule")) == f"'{shown_path}:3 [rule]'"
    assert str(Finding(path, 0, "rule")) == f"'{shown_path} [rule]'"


def _child(code, payload=b"", **kw):
    return run_child([sys.executable, "-I", "-c", code], payload, timeout=10, output_cap=1000, **kw)


def test_nonzero_child_exit_is_returned_as_is():
    out, timed_out, code = _child("import sys; sys.stdout.write('hi'); sys.exit(3)")
    assert (out, timed_out, code) == (b"hi", False, 3)


def test_child_does_not_see_the_parent_environment(monkeypatch):
    monkeypatch.setenv("CW_PARENT_SECRET_1399", "leak")
    code = "import os; print(sorted(os.environ))"
    out, _, rc = _child(code)
    assert rc == 0 and b"CW_PARENT_SECRET_1399" not in out and b"PATH" not in out


def test_timeout_returns_partial_output_and_kill_code():
    out, timed_out, code = run_child(
        [sys.executable, "-I", "-c", "import time,sys; print('p',flush=True); time.sleep(30)"],
        b"", timeout=1, output_cap=1000,
    )  # fmt: skip
    assert timed_out is True and code == -9 and out == b"p\n"


@pytest.mark.parametrize(
    "commit", ["HEAD", "abc123", "A" * 40, "0" * 39, "0" * 40 + "\xff", "0" * 41]
)
def test_commit_argument_must_be_a_full_hex_id(tmp_path, commit):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(GitCommandError):
        tree_differences(repo, commit, tmp_path)
