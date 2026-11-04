"""Rebuild the verifier's checkout, with the verifier's own code, inside the shadow directory.

The tests are laid over the candidate by ``deferred_tests.lay_tests_over`` (overlay-v1) and the
tree is cloned by ``workspace.create_attempt_clone``, exactly as ``verifiers`` does. All git work
happens in a private bare repository in the shadow directory that reads the project's objects
through an alternates file, so the project repository is never written. The rebuilt tree must
equal the recorded one (AC5), otherwise the run is UNCOMPARABLE."""

from __future__ import annotations

from pathlib import Path

from cairnwork.deferred_tests import TestCommitRejected, lay_tests_over, test_paths
from cairnwork.gitcmd import GitCommandError, run_git
from cairnwork.workspace import WorkspaceError, create_attempt_clone
from experimental.second_observer.inputs import ShadowInput, Uncomparable

ATTEMPT_ID = "shadow"


def _private_repo(shadow: Path, project: Path) -> Path:
    private = shadow / "repo.git"
    run_git(shadow, "init", "--quiet", "--bare", private.name)
    objects = run_git(project, "rev-parse", "--absolute-git-dir").strip() + "/objects"
    (private / "objects" / "info" / "alternates").write_text(objects + "\n")
    return private


def rebuild(inp: ShadowInput, shadow: Path, work: Path) -> Path:
    """The checkout directory, or Uncomparable."""
    try:
        private = _private_repo(shadow, inp.repo)
        for commit in (inp.base, inp.candidate, inp.test_commit):
            run_git(private, "cat-file", "-e", f"{commit}^{{commit}}")
        if inp.candidate != inp.base:  # a base run (candidate == base) has no fix commit
            parents = run_git(private, "rev-list", "--parents", "-n", "1", inp.candidate).split()
            if parents[1:] != [inp.base]:
                raise Uncomparable("candidate's only parent is not the base")
        paths = test_paths(private, inp.base, inp.test_commit, inp.test_roots)
        tested = lay_tests_over(private, inp.candidate, inp.test_commit, paths, ATTEMPT_ID, shadow)
        tree = run_git(private, "rev-parse", f"{tested}^{{tree}}").strip()
        if tree != inp.tested_tree:
            raise Uncomparable(f"rebuilt tree {tree} differs from the recorded {inp.tested_tree}")
        work.mkdir(mode=0o700)
        return create_attempt_clone(private, tested, work, ATTEMPT_ID).path
    except (GitCommandError, TestCommitRejected, WorkspaceError, OSError) as exc:
        raise Uncomparable(f"rebuild failed: {exc}") from exc
