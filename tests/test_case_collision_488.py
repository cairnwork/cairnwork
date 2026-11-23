"""#488: a test commit that adds a path differing only in case from a base path, or from another
added path, is refused with TEST_COMMIT_SCOPE (the commit is still consumed). A candidate's case
collision is not blamed on the base, and the cheap candidate checks run before the clone. Commits
are built from git objects only, so the tests mean the same on case-sensitive and case-insensitive
filesystems."""

import itertools
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import git

import cairnwork.verify_candidate as verify_candidate
import cairnwork.workspace as workspace
from cairnwork.deferred_tests import TestCommitRejected
from cairnwork.deferred_tests import test_paths as paths_of
from cairnwork.test_commit_paths import _keys
from cairnwork.workspace import (
    CandidateRejected,
    WorkspaceError,
    _collision_detail,
    create_attempt_clone,
)


def _commit(repo, parent, *names):
    """A commit on parent that adds names (one blob), built in a scratch index only."""
    git(repo, "config", "core.ignorecase", "false")
    git(repo, "read-tree", parent)
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for name in names:
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    return git(repo, "commit-tree", tree, "-p", parent, "-m", "c")


@pytest.fixture
def base(repo):
    return _commit(repo, "HEAD", "tests/test_parse.py")


def test_a_test_commit_adding_a_case_variant_is_refused(repo, base):
    tc = _commit(repo, base, "tests/test_Parse.py")
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"
    assert "test commit adds 'tests/test_Parse.py'" in str(err.value)
    assert "'tests/test_parse.py' in the base tree" in str(err.value)


def test_a_test_commit_adding_a_case_variant_directory_is_refused(repo, base):
    base = _commit(repo, base, "tests/sub/test_a.py")
    tc = _commit(repo, base, "tests/Sub/test_x.py")
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE" and "'tests/sub'" in str(err.value)


def test_a_non_colliding_new_test_file_passes(repo, base):
    tc = _commit(repo, base, "tests/test_other.py")
    assert paths_of(repo, base, tc) == (("tests/test_other.py",), ())


def test_modifying_the_existing_spelling_passes(repo, base):
    git(repo, "config", "core.ignorecase", "false")
    git(repo, "read-tree", base)
    other = git(repo, "rev-parse", "HEAD:src/b.py")
    git(repo, "update-index", "--cacheinfo", f"100644,{other},tests/test_parse.py")
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    tc = git(repo, "commit-tree", tree, "-p", base, "-m", "m")
    assert paths_of(repo, base, tc) == (("tests/test_parse.py",), ())


def test_a_base_that_already_collides_keeps_working_for_other_additions(repo):
    base = _commit(repo, "HEAD", "tests/test_a.py", "tests/test_A.py")
    tc = _commit(repo, base, "tests/test_new.py")
    assert paths_of(repo, base, tc) == (("tests/test_new.py",), ())


def test_the_detail_names_the_commit_not_the_base():
    detail = _collision_detail([["a.txt", "A.txt"]], ["missing a.txt"], "abc123")
    assert detail.startswith("tree of abc123 has case-colliding paths")
    assert "base tree" not in detail
    assert _collision_detail([["a", "A"]], []).startswith("tree of base has")


def test_a_colliding_candidate_is_not_blamed_on_the_base(repo, tmp_path, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    cand = _commit(repo, base, "src/X.py", "src/x.py")
    monkeypatch.setattr(workspace, "_folds_case", lambda clone: True)
    real = workspace.tree_differences
    monkeypatch.setattr(workspace, "tree_differences", lambda *a: [*real(*a), "missing src/x.py"])
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, cand, tmp_path / "attempts", "a1")
    assert f"tree of {cand} has case-colliding paths" in str(info.value)
    assert "base tree" not in str(info.value)


def test_the_candidate_is_checked_before_the_clone(repo, tmp_path, monkeypatch):
    base = git(repo, "rev-parse", "HEAD")
    cand = _commit(repo, base, "src/c.py")  # not in allowed_files
    cloned = []
    monkeypatch.setattr(verify_candidate, "create_attempt_clone", lambda *a, **k: cloned.append(a))
    runs = tmp_path / "runs"
    runs.mkdir()
    task: Any = SimpleNamespace(
        repo=repo, base_commit=base, test_roots=("tests",), allowed_files=("src/a.py",)
    )
    result: Any = SimpleNamespace(attempt_id="a1", logs=[])
    ctx: Any = SimpleNamespace(run_root=runs, python=None, account=None)
    with pytest.raises(CandidateRejected) as err:
        verify_candidate._judge_in(None, task, (), cand, None, result, ctx, source=repo)
    assert err.value.code == "OUT_OF_SCOPE" and cloned == []


@pytest.mark.parametrize(
    "names",
    [
        ("tests/test_n.py", "tests/test_N.py"),
        ("tests/Sub/a.py", "tests/sub/b.py"),
        ("tests/test_regression.py", "tests/test_Regression.py"),
    ],
)
def test_two_added_paths_differing_only_in_case_are_refused(repo, base, names):
    tc = _commit(repo, base, *names)
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"
    assert "differ only in case" in str(err.value)


def test_a_base_file_and_an_added_directory_of_another_case_are_refused(repo):
    base = _commit(repo, "HEAD", "tests/data")
    tc = _commit(repo, base, "tests/Data/x.py")
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"


@pytest.mark.parametrize(
    "names", [("tests/test_other.py",), ("tests/test_other.py", "tests/b/test_c.py")]
)
def test_unrelated_added_paths_still_pass(repo, base, names):
    tc = _commit(repo, base, *names)
    assert paths_of(repo, base, tc) == (tuple(sorted(names)), ())


def _swap(repo, parent, remove, add):
    """A commit on parent that removes some paths and adds others."""
    git(repo, "read-tree", parent)
    for name in remove:
        git(repo, "update-index", "--force-remove", name)
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for name in add:
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    return git(repo, "commit-tree", tree, "-p", parent, "-m", "c")


def test_adding_beside_a_same_case_sibling_directory_passes(repo):
    base = _commit(repo, "HEAD", "tests/sub/test_a.py")
    tc = _commit(repo, base, "tests/sub/test_y.py")
    assert paths_of(repo, base, tc) == (("tests/sub/test_y.py",), ())


@pytest.mark.parametrize(
    ("have", "remove", "add"),
    [
        (("tests/test_x.py",), ("tests/test_x.py",), ("tests/test_X.py",)),
        (("tests/sub/test_a.py",), ("tests/sub/test_a.py",), ("tests/Sub/test_b.py",)),
    ],
)
def test_a_pure_case_rename_passes(repo, have, remove, add):
    base = _commit(repo, "HEAD", *have)
    tc = _swap(repo, base, remove, add)
    assert paths_of(repo, base, tc) == (add, remove)


@pytest.mark.parametrize(
    ("have", "remove", "add"),
    [
        (("tests/test_x.py", "tests/test_y.py"), ("tests/test_y.py",), ("tests/test_X.py",)),
        (
            ("tests/sub/test_a.py", "tests/sub/test_c.py"),
            ("tests/sub/test_a.py",),
            ("tests/Sub/test_b.py",),
        ),
    ],
)
def test_a_case_variant_of_a_path_that_stays_is_refused(repo, have, remove, add):
    base = _commit(repo, "HEAD", *have)
    tc = _swap(repo, base, remove, add)
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"


# #1477: NFC and NFD spellings of one name (and their case variants) are one name on macOS.
# Data files (no verifier target needed), and the paths go in through --index-info on stdin: git
# on macOS precomposes the arguments, so a NFD name given as one would turn into its NFC form.
_NFC, _NFD, _CAPS = "caf\u00e9", "cafe\u0301", "CAFE\u0301"


def _commit_exact(repo, parent, *names):
    git(repo, "config", "core.ignorecase", "false")
    git(repo, "read-tree", parent)
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    entries = "".join(f"100644 {blob}\t{name}\0" for name in names)
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--add", "-z", "--index-info"],
        input=entries.encode(), check=True, capture_output=True,
    )  # fmt: skip
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    return git(repo, "commit-tree", tree, "-p", parent, "-m", "c")


@pytest.mark.parametrize(
    ("have", "add"),
    [
        (f"tests/data/{_NFC}.txt", f"tests/data/{_NFD}.txt"),
        (f"tests/data/{_NFD}.txt", f"tests/data/{_NFC}.txt"),
        (f"tests/data/{_NFC}.txt", f"tests/data/{_CAPS}.txt"),
        (f"tests/{_NFC}/a.txt", f"tests/{_NFD}/b.txt"),
    ],
)
def test_an_nfc_nfd_variant_of_a_base_path_is_refused(repo, have, add):
    base = _commit_exact(repo, "HEAD", have)
    tc = _commit_exact(repo, base, add)
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"


def test_an_nfc_nfd_pair_among_added_paths_is_refused(repo, base):
    tc = _commit_exact(repo, base, f"tests/data/{_NFC}.txt", f"tests/data/{_NFD}.txt")
    with pytest.raises(TestCommitRejected) as err:
        paths_of(repo, base, tc)
    assert err.value.code == "TEST_COMMIT_SCOPE"


def test_same_spelling_unicode_additions_still_pass(repo, base):
    tc = _commit_exact(repo, base, f"tests/data/{_NFC}.txt", f"tests/data/{_NFC}_b.txt")
    assert paths_of(repo, base, tc)[0] == (f"tests/data/{_NFC}.txt", f"tests/data/{_NFC}_b.txt")


def _clash_pairs() -> list[tuple[str, str]]:
    """Spellings a plain case fold equates but NFC-then-fold does not (U+0345 with unordered
    combining marks, the reviewer's 24 orders of four marks)."""
    marks = ["\u0345", "\u0301", "\u0323", "\u0307"]
    names = {"a" + "".join(o) for o in itertools.permutations(marks)}
    names |= {n.replace("\u0345", "\u03b9") for n in list(names)}
    pairs = [
        (one, two)
        for one, two in itertools.combinations(sorted(names), 2)
        if one.casefold() == two.casefold() and _keys(one)[1] != _keys(two)[1]
    ]
    return pairs[:6]


def test_the_fold_only_pairs_exist_and_are_refused_both_ways(repo, base):
    pairs = _clash_pairs()
    assert pairs  # the shape the reviewer found: equal by casefold, different by NFC-then-fold
    for one, two in pairs:
        tc = _commit_exact(repo, base, f"tests/data/{one}.txt", f"tests/data/{two}.txt")
        with pytest.raises(TestCommitRejected):
            paths_of(repo, base, tc)
        for have, add in ((one, two), (two, one)):
            held = _commit_exact(repo, base, f"tests/data/{have}.txt")
            tc = _commit_exact(repo, held, f"tests/data/{add}.txt")
            with pytest.raises(TestCommitRejected):
                paths_of(repo, held, tc)


@pytest.mark.parametrize(
    ("one", "two"),
    [
        ("x\u0390", "X\u03aa\u0301"),
        ("a\u0323\u0345", "a\u0345\u0323"),
        (f"{_NFC}", f"{_CAPS}"),
    ],
)
def test_unicode_fold_variants_are_refused_in_both_directions(repo, base, one, two):
    for have, add in ((one, two), (two, one)):
        held = _commit_exact(repo, base, f"tests/data/{have}.txt")
        tc = _commit_exact(repo, held, f"tests/data/{add}.txt")
        with pytest.raises(TestCommitRejected) as err:
            paths_of(repo, held, tc)
        assert err.value.code == "TEST_COMMIT_SCOPE"
    tc = _commit_exact(repo, base, f"tests/data/{one}.txt", f"tests/data/{two}.txt")
    with pytest.raises(TestCommitRejected):
        paths_of(repo, base, tc)


@pytest.mark.parametrize(
    ("one", "two"),
    [("\uff21", "A"), ("\u2460", "1"), ("\u3131", "\u1100"), (_NFC, "cafe")],
)
def test_names_that_are_not_one_name_on_disk_both_pass(repo, base, one, two):
    # compatibility forms (NFKC) and a plain letter are different names: must not be merged
    held = _commit_exact(repo, base, f"tests/data/{one}.txt")
    tc = _commit_exact(repo, held, f"tests/data/{two}.txt")
    assert paths_of(repo, held, tc)[0] == (f"tests/data/{two}.txt",)


def test_deleting_the_nfd_name_and_adding_the_nfc_name_passes(repo, base):
    held = _commit_exact(repo, base, f"tests/data/{_NFD}.txt")
    tc = _swap_exact(repo, held, f"tests/data/{_NFD}.txt", f"tests/data/{_NFC}.txt")
    assert paths_of(repo, held, tc) == ((f"tests/data/{_NFC}.txt",), (f"tests/data/{_NFD}.txt",))


def _swap_exact(repo, parent, remove, add):
    # two calls: in one, git's index (which folds NFC/NFD on macOS) drops the add after the remove
    git(repo, "config", "core.ignorecase", "false")
    git(repo, "read-tree", parent)
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for flag, entry in (
        ("--remove", f"0 {'0' * 40}\t{remove}\0"),
        ("--add", f"100644 {blob}\t{add}\0"),
    ):
        subprocess.run(
            ["git", "-C", str(repo), "update-index", flag, "-z", "--index-info"],
            input=entry.encode(), check=True, capture_output=True,
        )  # fmt: skip
    tree = git(repo, "write-tree")
    git(repo, "read-tree", "HEAD")
    return git(repo, "commit-tree", tree, "-p", parent, "-m", "s")
