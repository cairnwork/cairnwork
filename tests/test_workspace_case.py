"""#428: a base tree with paths that differ only in case, on a case-insensitive filesystem,
is refused with that reason rather than the generic "not a clean checkout"."""

import itertools
import os
import unicodedata

import pytest
from conftest import git

import cairnwork.workspace as workspace
from cairnwork.workspace import (
    WorkspaceError,
    _case_collisions,
    _collision_detail,
    create_attempt_clone,
)


def _folds_case(directory):
    probe = directory / "CaseProbe"
    probe.write_text("")
    try:
        return os.path.exists(directory / "caseprobe")
    finally:
        probe.unlink()


def _commit_with(repo, *names):
    """Commit base plus names (same content) built in the index only: the files never exist."""
    git(repo, "config", "core.ignorecase", "false")
    blob = git(repo, "rev-parse", "HEAD:src/a.py")
    for name in names:
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    tree = git(repo, "write-tree")
    return git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "case")


def test_case_colliding_files_are_named(repo, tmp_path):
    base = _commit_with(repo, "docs/X.html", "docs/x.html", "::warning::A", "::WARNING::a")
    if not _folds_case(tmp_path):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")  # nothing collides
        return
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    message = str(info.value)
    assert f"is not a clean checkout of {base}: tree of {base} has case-colliding paths" in message
    assert "; differences: 'missing " in message  # every difference is still listed
    assert "on a case-insensitive filesystem" in message
    assert "('docs/X.html', 'docs/x.html')" in message
    assert "('::WARNING::a', '::warning::A')" in message  # quoted, never a bare '::' line


def test_case_colliding_directories_are_named(repo, tmp_path):
    base = _commit_with(repo, "Docs/a.html", "docs/b.html")
    if not _folds_case(tmp_path):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
        return
    with pytest.raises(WorkspaceError, match=r"case-colliding paths \('Docs', 'docs'\)"):
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")


def test_a_base_without_collisions_still_clones(repo, tmp_path):
    base = _commit_with(repo, "docs/X.html", "docs/y.html")
    made = create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    assert git(made.path, "rev-parse", "HEAD") == base


def _with_extra(monkeypatch, *extra):
    real = workspace.tree_differences
    monkeypatch.setattr(workspace, "tree_differences", lambda *a: [*real(*a), *extra])


def test_a_difference_under_a_colliding_directory_is_still_listed(repo, tmp_path, monkeypatch):
    """The collision only adds to the error: an unrelated leftover, even inside a colliding
    directory, is always shown. _folds_case is forced so this runs on every filesystem."""
    base = _commit_with(repo, "Docs/a.html", "docs/b.html")
    monkeypatch.setattr(workspace, "_folds_case", lambda clone: True)
    _with_extra(monkeypatch, "extra directory DOCS/sub", "extra ::warning::left.txt")
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    message = str(info.value)
    assert f"is not a clean checkout of {base}: tree of {base} has case-colliding paths" in message
    assert "'extra directory DOCS/sub'" in message
    assert "'extra ::warning::left.txt'" in message


def test_a_head_other_than_base_keeps_the_generic_error(repo, tmp_path, monkeypatch):
    base = _commit_with(repo, "docs/X.html", "docs/x.html")
    monkeypatch.setattr(workspace, "_folds_case", lambda clone: True)
    real = workspace.run_git
    monkeypatch.setattr(
        workspace,
        "run_git",
        lambda path, *a, **k: "0" * 40 if a == ("rev-parse", "HEAD") else real(path, *a, **k),
    )
    with pytest.raises(WorkspaceError) as info:
        create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    assert str(info.value).endswith(f"is not a clean checkout of '{base}'")


# The message itself, independent of the host's filesystem.


def test_case_collisions_group_files_and_directories():
    names = ["Docs/a.html", "docs/b.html", "lib/X.py", "lib/x.py", "y.py"]
    assert _case_collisions(names) == [["Docs", "docs"], ["lib/X.py", "lib/x.py"]]
    assert _case_collisions(["a/X", "b/x", "c"]) == []


def test_case_collisions_group_nfc_and_nfd_spellings_too():
    # one name on APFS (#1714): an NFC and an NFD spelling, with or without a case difference
    nfc, nfd = "caf\u00e9.py", "cafe\u0301.py"
    assert _case_collisions([f"d/{nfc}", f"d/{nfd}"]) == [sorted([f"d/{nfc}", f"d/{nfd}"])]
    assert _case_collisions(["D\u00c9.py", "de\u0301.py"]) == [["D\u00c9.py", "de\u0301.py"]]
    assert _case_collisions(["\u00e9/x.py", "e\u0301/y.py"]) == [["e\u0301", "\u00e9"]]


def test_case_collisions_keep_different_names_apart():
    assert _case_collisions(["cafe.py", "caf\u00e9.py"]) == []  # cafe is not café
    assert _case_collisions(["\uff21.py", "A.py"]) == []  # NFKC pairs are different names
    assert _case_collisions(["\u2460.py", "1.py"]) == []


def _nfc_key(name):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def test_case_collisions_match_on_either_key():
    # U+0345 with unordered combining marks: equal by case fold, not by the NFC key alone
    marks = ["\u0345", "\u0301", "\u0323", "\u0307"]
    names = {"a" + "".join(order) for order in itertools.permutations(marks)}
    names |= {n.replace("\u0345", "\u03b9") for n in list(names)}
    pairs = [
        (one, two)
        for one, two in itertools.combinations(sorted(names), 2)
        if one.casefold() == two.casefold() and _nfc_key(one) != _nfc_key(two)
    ]
    assert pairs  # the shape exists
    for one, two in pairs[:6]:
        assert _case_collisions([one, two]) == [sorted([one, two])]


def test_the_detail_caps_groups_and_lists_every_kind_of_difference():
    groups = [[f"{c}.txt", f"{c.upper()}.txt"] for c in "abcd"]
    differences = [f"extra ::{n}" for n in range(7)]
    detail = _collision_detail(groups, differences)
    assert "('a.txt', 'A.txt'), ('b.txt', 'B.txt'), ('c.txt', 'C.txt') and 1 more" in detail
    shown = ", ".join(repr(d) for d in differences[:5])
    assert detail.endswith(f"; differences: {shown} and 2 more")
    assert _collision_detail(groups[:1], ["missing a.txt"]).endswith("differences: 'missing a.txt'")
