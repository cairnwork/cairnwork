"""#300: the tree check and the shadow gate share one import-path normalizer.

``import_path_tree.require_plain_dirs`` (#293) walks each import path in the git tree, and
``verifier_spec.protected_verifier_inputs`` (#290) protects first-level modules under the same
paths. If the two read an entry differently, the gate protects one directory while the verifier
imports from another. Both now call ``import_path_tree.normalize_import_paths``.
"""

import pytest
from conftest import commit_change

from cairnwork import import_path_tree, verifier_spec
from cairnwork.import_path_tree import ImportPathError, normalize_import_paths, require_plain_dirs
from cairnwork.verifier_spec import protected_verifier_inputs

BAD_ENTRIES = [
    "a/./b",
    "a//b",
    "a/../a/b",
    "./a/./b",
    "a/..",
    "../outside",
    "/abs",
    "//abs",
    "café",
    "café",
    ";",
    "src/K",
]


def test_both_modules_use_the_same_normalizer():
    assert verifier_spec.normalize_import_paths is import_path_tree.normalize_import_paths


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_a_bad_entry_is_refused_by_the_tree_check(repo, entry):
    commit = commit_change(repo, {"a/b/m.py": "x = 1\n"})
    with pytest.raises(ImportPathError) as exc:
        require_plain_dirs(repo, commit, (entry,))
    assert exc.value.code == "BAD_IMPORT_PATH"


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_the_same_bad_entry_is_refused_by_the_shadow_gate(entry):
    with pytest.raises(ImportPathError) as exc:
        protected_verifier_inputs(("a/b/colorsys.py",), import_paths=(entry,))
    assert exc.value.code == "BAD_IMPORT_PATH"


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_a_bad_entry_is_refused_even_next_to_a_good_one(entry):
    with pytest.raises(ImportPathError):
        normalize_import_paths(("src", entry))


@pytest.mark.parametrize("entry", ["src", "./src", "src/", "./src/", "././src//"])
def test_spellings_of_one_dir_normalize_alike_in_both(repo, entry):
    assert normalize_import_paths((entry,)) == ("src",)
    commit = commit_change(repo, {"src/m.py": "x = 1\n"})
    require_plain_dirs(repo, commit, (entry,))
    protected = protected_verifier_inputs(("src/colorsys.py",), import_paths=(entry,))
    assert protected == ("src/colorsys.py",)


@pytest.mark.parametrize("entry", ["", ".", "./", ".//"])
def test_the_checkout_root_is_dropped(entry):
    assert normalize_import_paths((entry,)) == ()


def test_duplicates_collapse_in_order():
    assert normalize_import_paths(("lib", "src/", "./lib")) == ("lib", "src")


def test_the_shadow_gate_keeps_whitespace_like_the_verifier_path():
    """The verifier's sys.path entry is ``<checkout>/ src`` (pytest_config joins the entry as
    is), so the gate must protect `` src/colorsys.py``, not ``src/colorsys.py``."""
    assert normalize_import_paths((" src",)) == (" src",)
    assert protected_verifier_inputs((" src/colorsys.py",), import_paths=(" src",)) == (
        " src/colorsys.py",
    )
