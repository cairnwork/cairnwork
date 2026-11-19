"""Test helper for candidates (#1394); kept out of conftest.py, which the #1353 witness pins."""

from conftest import commit_change, git


def commit_candidate(repo, files=None, attempt="att-1", **kwargs):
    """Commit a candidate to ``refs/cairnwork/attempts/<attempt>`` and leave the clone's
    ``main`` (and the work tree) where they were (#1394). A candidate committed on ``main`` makes
    the mirror's local ``main`` ahead of ``origin/main``, which ``lineage.verify_single_branch``
    refuses (#1266). Returns the commit id; takes the arguments of ``commit_change``."""
    head = git(repo, "symbolic-ref", "-q", "HEAD")
    before = git(repo, "rev-parse", "HEAD")
    commit = commit_change(repo, files, **kwargs)
    git(repo, "update-ref", f"refs/cairnwork/attempts/{attempt}", commit)
    git(repo, "update-ref", head, before)
    git(repo, "reset", "-q", "--hard", before)
    return commit
