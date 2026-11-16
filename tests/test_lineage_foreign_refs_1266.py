"""#1266: the single-branch check refuses history held outside branches (tags, notes, replace, ...).

A ref outside refs/heads and refs/remotes may name only a commit in origin/main's history.
Tightening only: the branch checks are unchanged.
"""

import pytest
import test_issue_runner as shared
from conftest import commit_change, git

import cairnwork.lineage as lineage

setup = shared.setup


def _side(clone):
    """A commit that is on no branch of the mirror."""
    git(clone, "checkout", "-q", "-b", "side")
    sha = commit_change(clone, {"side.txt": "x\n"})
    git(clone, "checkout", "-q", "main")
    git(clone, "branch", "-q", "-D", "side")
    return sha


def _tree(clone):
    return git(clone, "rev-parse", "HEAD^{tree}")


def _must_refuse(clone):
    with pytest.raises(RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH.*not in main"):
        lineage.verify_single_branch(clone)


@pytest.mark.parametrize(
    "make",
    [
        lambda c, side: git(c, "update-ref", "refs/tags/v1", side),
        lambda c, side: git(c, "tag", "-a", "-m", "m", "v1", side),
        lambda c, side: git(c, "update-ref", "refs/tags/tree", _tree(c)),
        lambda c, side: git(c, "update-ref", "refs/notes/x", side),
        lambda c, side: git(c, "update-ref", "refs/stash", side),
        lambda c, side: git(c, "update-ref", "refs/hidden/deferred", side),
        lambda c, side: git(c, "update-ref", "refs/replace/" + "0" * 40, side),
    ],
    ids=["tag", "annotated-tag", "tree-tag", "notes", "stash", "other-namespace", "replace"],
)
def test_a_ref_outside_main_history_is_refused(setup, make):
    clone = setup["clone"]
    make(clone, _side(clone))
    _must_refuse(clone)


def test_a_replace_ref_is_refused_even_onto_main_history(setup):
    clone = setup["clone"]
    head = git(clone, "rev-parse", "HEAD")
    git(clone, "update-ref", "refs/replace/" + head, head)
    _must_refuse(clone)


@pytest.mark.parametrize("annotated", [False, True], ids=["lightweight", "annotated"])
def test_a_tag_in_main_history_passes(setup, annotated):
    clone = setup["clone"]
    if annotated:
        git(clone, "tag", "-a", "-m", "m", "v1", "origin/main")
    else:
        git(clone, "tag", "v1", "origin/main")
    lineage.verify_single_branch(clone)


def test_a_clean_single_branch_mirror_still_passes(setup):
    lineage.verify_single_branch(setup["clone"])


def test_the_own_pin_namespace_is_left_to_its_owner(setup):
    clone = setup["clone"]
    git(clone, "update-ref", "refs/cairnwork-bases/" + "a" * 40, _side(clone))
    lineage.verify_single_branch(clone)  # open question in #1266: not judged here


def test_a_grafts_file_cannot_make_a_side_commit_pass_as_main_history(setup):
    clone = setup["clone"]
    side = _side(clone)
    git(clone, "update-ref", "refs/tags/v1", side)
    main_tip = git(clone, "rev-parse", "origin/main")
    grafts = clone / ".git" / "info" / "grafts"
    grafts.parent.mkdir(exist_ok=True)
    grafts.write_text(f"{main_tip} {side}\n")  # main's tip now "descends from" the side commit
    with pytest.raises(RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH.*grafts"):
        lineage.verify_single_branch(clone)


def test_an_empty_grafts_file_is_refused_too(setup):
    clone = setup["clone"]
    grafts = clone / ".git" / "info" / "grafts"
    grafts.parent.mkdir(exist_ok=True)
    grafts.write_text("")
    with pytest.raises(RuntimeError, match="grafts"):
        lineage.verify_single_branch(clone)
