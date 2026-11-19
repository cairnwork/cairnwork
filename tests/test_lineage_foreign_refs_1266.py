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


def _advance_origin(clone):
    """origin/main moves one commit ahead of local main, so main is an ancestor of it."""
    git(clone, "checkout", "-q", "-b", "adv")
    commit_change(clone, {"adv.txt": "x\n"})
    git(clone, "update-ref", "refs/remotes/origin/main", "adv")
    git(clone, "checkout", "-q", "main")
    git(clone, "branch", "-q", "-D", "adv")


def _origin_head_to(clone, side):
    git(clone, "update-ref", "--no-deref", "refs/remotes/origin/HEAD", side)


def _pack_only(clone, ref, target):
    """Plant ``ref`` as a line in packed-refs only (no loose file) (#1390)."""
    packed = clone / ".git" / "packed-refs"
    old = (
        packed.read_text() if packed.exists() else "# pack-refs with: peeled fully-peeled sorted \n"
    )
    packed.write_text(old + f"{target} {ref}\n")
    assert not (clone / ".git" / ref).exists()


def _update_ref_then_pack(clone, ref, target):
    git(clone, "update-ref", ref, target)
    git(clone, "pack-refs", "--all")
    assert not (clone / ".git" / ref).exists()


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
    "attempt-max-length-id": lambda c, side: git(
        c, "update-ref", "refs/cairnwork/attempts/" + "a" * 64, side
    ),
    "main-behind-origin-main": lambda c, side: _advance_origin(c),
    "head-detached-on-main-history": lambda c, side: git(
        c, "update-ref", "--no-deref", "HEAD", "origin/main"
    ),
    "origin-head-at-origin-main": lambda c, side: git(
        c, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"
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
    "replace-off-main-packed-only": lambda c, side: _pack_only(c, "refs/replace/" + SHA40, side),
    "replace-onto-main-packed-only": lambda c, side: _pack_only(
        c, "refs/replace/" + git(c, "rev-parse", "HEAD"), git(c, "rev-parse", "HEAD")
    ),
    "replace-update-ref-then-pack-refs": lambda c, side: _update_ref_then_pack(
        c, "refs/replace/" + SHA40, side
    ),
    "head-on-another-branch": lambda c, side: git(c, "symbolic-ref", "HEAD", "refs/heads/other"),
    "head-on-unborn-main": lambda c, side: git(c, "update-ref", "-d", "refs/heads/main"),
    "head-detached-on-side-commit": lambda c, side: git(
        c, "update-ref", "--no-deref", "HEAD", side
    ),
    "cairnwork-test-ref": lambda c, side: git(c, "update-ref", "refs/cairnwork/test", side),
    "attempt-lookalike": lambda c, side: git(c, "update-ref", "refs/cairnwork/attemptsX/a", side),
    "stray-branch": lambda c, side: git(c, "update-ref", "refs/heads/feature", side),
    "other-remote": lambda c, side: git(c, "update-ref", "refs/remotes/upstream/main", side),
    "main-on-side-commit": lambda c, side: git(c, "update-ref", "refs/heads/main", side),
    "origin-head-on-side-commit": _origin_head_to,
    "attempt-nested": lambda c, side: git(c, "update-ref", "refs/cairnwork/attempts/x/y", side),
    "attempt-bad-id-char": lambda c, side: git(
        c, "update-ref", "refs/cairnwork/attempts/a.b", side
    ),
    "attempt-id-leading-dash": lambda c, side: git(
        c, "update-ref", "refs/cairnwork/attempts/-a", side
    ),
    "attempt-id-too-long": lambda c, side: git(
        c, "update-ref", "refs/cairnwork/attempts/" + "a" * 65, side
    ),
    "bases-short": lambda c, side: git(c, "update-ref", "refs/cairnwork-bases/" + "a" * 39, side),
    "bases-uppercase": lambda c, side: git(
        c, "update-ref", "refs/cairnwork-bases/" + "A" * 40, side
    ),
    "bases-slash": lambda c, side: git(
        c, "update-ref", "refs/cairnwork-bases/" + "a" * 20 + "/" + "a" * 19, side
    ),
    "bases-too-long": lambda c, side: git(
        c, "update-ref", "refs/cairnwork-bases/" + "a" * 41, side
    ),
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


@pytest.mark.parametrize(
    "ref",
    ["refs/heads/main", "refs/remotes/origin/HEAD"],
)
def test_allowed_branch_refs_off_main_are_refused(setup, ref):
    """The names are allowed, but a tip outside origin/main history is not."""
    clone = setup["clone"]
    git(clone, "update-ref", "--no-deref", ref, _side(clone))
    with pytest.raises(RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH"):
        lineage.verify_single_branch(clone)


@pytest.mark.parametrize("prefix", ["refs/cairnwork-basesX/", "refs/cairnwork-bases-/"])
def test_a_lookalike_bases_namespace_is_refused(setup, prefix):
    clone = setup["clone"]
    git(clone, "update-ref", prefix + "a" * 40, _side(clone))
    with pytest.raises(RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH"):
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
