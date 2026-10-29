"""#417: Cairnwork's own clones copy no git template directory.

A template brings ~20 files per clone (hook samples, info/exclude, description) and, through an
operator's ``init.templateDir``, could bring executable hooks into a worker's or verifier's .git.
"""

from types import SimpleNamespace
from typing import Any

from conftest import commit_change, git

from cairnwork.verify_candidate import _scratch
from cairnwork.workspace import create_attempt_clone


def _assert_no_template_files(clone):
    assert (clone / ".git").is_dir()
    assert not list((clone / ".git").glob("hooks/*"))
    assert not (clone / ".git" / "info" / "exclude").exists()
    assert not (clone / ".git" / "description").exists()


def test_an_attempt_clone_has_no_hook_samples(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    made = create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    _assert_no_template_files(made.path)


def test_a_verify_scratch_clone_has_no_hook_samples(repo, tmp_path):
    base = git(repo, "rev-parse", "HEAD")
    candidate = commit_change(repo, {"src/a.py": "x = 2\n"})
    (tmp_path / "state").mkdir()
    intake: Any = SimpleNamespace(state=tmp_path / "state")
    task: Any = SimpleNamespace(base_commit=base)
    with _scratch(intake, repo, repo, task, candidate, "v1") as clone:
        assert git(clone, "cat-file", "-t", candidate) == "commit"
        _assert_no_template_files(clone)
