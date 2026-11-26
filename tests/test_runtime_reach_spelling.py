"""#1854 review: a file-less plugin name and a spelling variant of its file (Unicode form, non-ASCII
case fold; only the spellings main refuses are rows here). The verdict is never wider than
main's: every row marked refused is one main refuses. Each family is tried as an add, a change
and a delete."""

import unicodedata as ud
from pathlib import Path

import pytest

from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.runtime_reach import check_runtime_reach

NFC, NFD = ud.normalize("NFC", "café"), ud.normalize("NFD", "café")
ANGSTROM, A_RING = "Å", "Å"  # the Angstrom sign normalises to A-ring
# (plugin name, the base's spelling, a variant of it)
FAMILIES = {
    "nfd": (f"pkg.{NFC}", f"pkg/{NFC}.py", f"pkg/{NFD}.py"),
    "nfc-of-nfd": (f"pkg.{NFD}", f"pkg/{NFD}.py", f"pkg/{NFC}.py"),
    "sharp-s": ("pkg.straße", "pkg/straße.py", "pkg/STRASSE.py"),
    "ligature": ("pkg.ﬁle", "pkg/ﬁle.py", "pkg/FILE.py"),
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = (tmp_path / "checkout").resolve()
    path.mkdir()
    return path


def verdict(root, name, changed, added, base) -> bool:
    record = CollectedRecord(frozenset(), ((name, None),), (), (), str(root))
    try:
        check_runtime_reach(record, changed, added, base_paths=frozenset(base))
    except TestCommitRejected:
        return True
    return False


def operations(canonical, variant):
    """(changed, added, base) for the variant being added, changed and deleted."""
    return {
        "add": ([], [variant], {canonical}),
        "change-both-in-base": ([variant], [], {canonical, variant}),
        "change-only-variant-in-base": ([variant], [], {variant}),
        "delete-both-in-base": ([variant], [], {canonical, variant}),  # a delete is in changed
    }


@pytest.mark.parametrize("op", ["add", "change-both-in-base", "change-only-variant-in-base"])
@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_spelling_variant_of_a_named_plugin_file_is_refused(root, family, op):
    name, canonical, variant = FAMILIES[family]
    changed, added, base = operations(canonical, variant)[op]
    assert verdict(root, name, changed, added, base)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_deleted_spelling_variant_of_a_named_plugin_file_is_refused(root, family):
    name, canonical, variant = FAMILIES[family]
    changed, added, base = operations(canonical, variant)["delete-both-in-base"]
    assert verdict(root, name, changed, added, base)


@pytest.mark.parametrize(
    ("changed", "added", "base"),
    [
        ([], ["pkg/Mod.py"], {"pkg/mod.py"}),  # ASCII case: main accepts it too (Issue)
        (["pkg/Mod.py"], [], {"pkg/mod.py", "pkg/Mod.py"}),
        ([], ["pkg/other.py"], {"pkg/mod.py"}),
        (["pkg/other.py"], [], {"pkg/mod.py", "pkg/other.py"}),
    ],
)
def test_what_main_accepts_is_still_accepted(root, changed, added, base):
    assert not verdict(root, "pkg.mod", changed, added, base)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_an_unrelated_file_next_to_a_variant_pair_is_accepted(root, family):
    name, canonical, variant = FAMILIES[family]
    assert not verdict(root, name, [], ["pkg/unrelated.py"], {canonical, variant})
    assert not verdict(
        root, name, ["pkg/unrelated.py"], [], {canonical, variant, "pkg/unrelated.py"}
    )
