"""#300: shared import path normalizer between import_path_tree and verifier_spec."""

from cairnwork import import_path_tree, verifier_spec
from cairnwork.import_path_tree import normalize_import_paths
from cairnwork.verifier_spec import protected_verifier_inputs


def test_verifier_spec_and_import_path_tree_share_function():
    """a. verifier_spec and import_path_tree use the same function."""
    assert not hasattr(verifier_spec, "_normalize_import_paths")
    assert hasattr(import_path_tree, "normalize_import_paths")


def test_verifier_spec_preserves_whitespace():
    """b. verifier_spec preserves whitespace in import path entries."""
    assert protected_verifier_inputs(paths=(" src/x.pth",), import_paths=(" src",)) == (
        " src/x.pth",
    )


def test_trailing_slash_and_dot_slash():
    """c. trailing slash and ./ in import_paths still protects path."""
    assert protected_verifier_inputs(paths=("src/x.pth",), import_paths=("./src/",)) == (
        "src/x.pth",
    )


def test_normalize_import_paths_dedup_and_clean():
    """d. normalize_import_paths cleans leading ./, trailing /, drops empty/. and dedups."""
    assert normalize_import_paths((".", "", "./", "src", "src/")) == ("src",)


def test_repo_root_preserved():
    """e. repo root '' is preserved when import_paths is empty."""
    assert protected_verifier_inputs(paths=("x.pth",), import_paths=()) == ("x.pth",)
