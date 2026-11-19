"""``commit_candidate`` (tests/candidate_helpers.py, #1394): the candidate sits on an attempts
ref, ``main`` and the work tree stay on the base (the mirror passes ``verify_single_branch``)."""

from candidate_helpers import commit_candidate
from conftest import git


def test_candidate_is_on_its_attempt_ref_and_main_is_unchanged(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit = commit_candidate(repo, {"src/a.py": "x = 2\n"}, attempt="att-7")
    assert commit != base
    assert git(repo, "rev-parse", "refs/cairnwork/attempts/att-7") == commit
    assert git(repo, "rev-parse", "main") == base
    assert git(repo, "rev-parse", "HEAD") == base
    assert (repo / "src" / "a.py").read_text() == "x = 1\n"  # the work tree is the base again
    assert git(repo, "show", f"{commit}:src/a.py") == "x = 2"


def test_default_attempt_name_and_commit_change_options(repo):
    commit = commit_candidate(repo, delete=("src/b.py",), message="drop b")
    assert git(repo, "rev-parse", "refs/cairnwork/attempts/att-1") == commit
    assert git(repo, "log", "-1", "--format=%s", commit) == "drop b"
    assert (repo / "src" / "b.py").exists()  # restored with the base


def test_two_attempts_do_not_move_main(repo):
    base = git(repo, "rev-parse", "main")
    one = commit_candidate(repo, {"src/a.py": "x = 2\n"}, attempt="att-1")
    two = commit_candidate(repo, {"src/a.py": "x = 3\n"}, attempt="att-2")
    assert {git(repo, "rev-parse", f"refs/cairnwork/attempts/att-{n}") for n in (1, 2)} == {
        one,
        two,
    }
    assert git(repo, "rev-parse", "main") == base
