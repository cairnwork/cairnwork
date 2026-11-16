"""#1266: the single-branch check judges every ref and refuses a grafts file.

The table is the Gemini audit CW-AUDIT-LINEAGE-1 (layer1 11/16): which refs a mirror or working
clone may hold. Product-written namespaces pass, foreign history does not. Tightening only.
"""

import pytest
import test_issue_runner as shared
from conftest import commit_change, git

import cairnwork.lineage as lineage

setup = shared.setup
SHA40 = "0" * 40


def _side(clone):
    """A commit on no branch of the mirror."""
    git(clone, "checkout", "-q", "-b", "side")
    sha = commit_change(clone, {"side.txt": "x\n"})
    git(clone, "checkout", "-q", "main")
    git(clone, "branch", "-q", "-D", "side")
    return sha


def _grafts(clone, text):
    path = clone / ".git" / "info" / "grafts"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)


MUST_PASS = {
    "tag-on-main": lambda c, side: git(c, "update-ref", "refs/tags/v1", "origin/main"),
    "annotated-tag-on-main": lambda c, side: git(c, "tag", "-a", "-m", "m", "v1", "origin/main"),
    "bases-pin-off-main": lambda c, side: git(
        c, "update-ref", "refs/cairnwork-bases/" + "a" * 40, side
    ),
    "attempt": lambda c, side: git(c, "update-ref", "refs/cairnwork/attempts/att-1", side),
    "attempt-tested": lambda c, side: git(
        c, "update-ref", "refs/cairnwork/attempts/att-1-tested", side
    ),
}
MUST_BLOCK = {
    "tag-on-side-commit": lambda c, side: git(c, "update-ref", "refs/tags/secret", side),
    "annotated-tag-on-side": lambda c, side: git(c, "tag", "-a", "-m", "m", "secret", side),
    "tag-on-tree": lambda c, side: git(c, "update-ref", "refs/tags/tree", "origin/main^{tree}"),
    "notes": lambda c, side: git(c, "update-ref", "refs/notes/commits", side),
    "stash": lambda c, side: git(c, "update-ref", "refs/stash", side),
    "hidden-namespace": lambda c, side: git(c, "update-ref", "refs/hidden/deferred", side),
    "replace-off-main": lambda c, side: git(c, "update-ref", "refs/replace/" + SHA40, side),
    "replace-onto-main": lambda c, side: git(
        c, "update-ref", "refs/replace/" + git(c, "rev-parse", "HEAD"), git(c, "rev-parse", "HEAD")
    ),
    "cairnwork-test-ref": lambda c, side: git(c, "update-ref", "refs/cairnwork/test", side),
    "attempt-lookalike": lambda c, side: git(c, "update-ref", "refs/cairnwork/attemptsX/a", side),
    "stray-branch": lambda c, side: git(c, "update-ref", "refs/heads/feature", side),
    "other-remote": lambda c, side: git(c, "update-ref", "refs/remotes/upstream/main", side),
    "grafts-file": lambda c, side: _grafts(c, f"{git(c, 'rev-parse', 'origin/main')} {side}\n"),
    "empty-grafts-file": lambda c, side: _grafts(c, ""),
    "tag-made-to-look-like-main-by-grafts": lambda c, side: (
        git(c, "update-ref", "refs/tags/v1", side),
        _grafts(c, f"{git(c, 'rev-parse', 'origin/main')} {side}\n"),
    ),
}


@pytest.mark.parametrize("make", MUST_PASS.values(), ids=MUST_PASS.keys())
def test_a_ref_the_product_or_main_history_owns_passes(setup, make):
    clone = setup["clone"]
    make(clone, _side(clone))
    lineage.verify_single_branch(clone)


@pytest.mark.parametrize("make", MUST_BLOCK.values(), ids=MUST_BLOCK.keys())
def test_foreign_history_is_refused(setup, make):
    clone = setup["clone"]
    make(clone, _side(clone))
    with pytest.raises(
        RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH|LINEAGE_GRAFTS_REFUSED"
    ):
        lineage.verify_single_branch(clone)


def test_a_clean_single_branch_mirror_still_passes(setup):
    lineage.verify_single_branch(setup["clone"])


def test_an_accepted_attempt_does_not_poison_the_next_check(setup):
    """The ref a collected candidate leaves behind must not make the next take() refuse."""
    clone = setup["clone"]
    side = _side(clone)
    git(clone, "update-ref", "refs/cairnwork/attempts/att-1", side)
    git(clone, "update-ref", "refs/cairnwork/attempts/att-1-tested", side)
    lineage.verify_single_branch(clone)
    lineage.verify_single_branch(clone)


def test_require_on_main_refuses_a_grafts_file(setup):
    """A base that grafts made an ancestor of main is not on main (intake reaches this directly)."""
    clone = setup["clone"]
    side = _side(clone)
    _grafts(clone, f"{git(clone, 'rev-parse', 'origin/main')} {side}\n")
    with pytest.raises(RuntimeError, match="LINEAGE_GRAFTS_REFUSED"):
        lineage.require_on_main(clone, side, fetch=False)
