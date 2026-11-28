"""#1869: deleting a package marker where a standard-library-named file already sits is refused,
because the file then becomes a top-level import root. Real git trees, real gate."""

import pytest

from tests.test_candidate_stdlib_root_1829 import BODY, TEST, _gate

BASE = {"tests/test_a.py": TEST, "pkgdir/__init__.py": "", "pkgdir/m.py": BODY}


@pytest.mark.parametrize(
    ("neighbour", "marker"),
    [
        pytest.param("pkgdir/colorsys.py", "pkgdir/__init__.py", id="module-file"),
        pytest.param("pkgdir/COLORSYS.py", "pkgdir/__init__.py", id="case-insensitive"),
        pytest.param("pkgdir/colorsys/__init__.py", "pkgdir/__init__.py", id="package-form"),
        pytest.param("pkgdir/pytest.py", "pkgdir/__init__.py", id="pytest-family"),
        pytest.param("pkgdir/colorsys.cpython-314-darwin.so", "pkgdir/__init__.py", id="compiled"),
        pytest.param("colorsys.py", "__init__.py", id="repo-root"),
        pytest.param("Pkgdir/colorsys.py", "pkgdir/__init__.py", id="dir-case-differs"),
        pytest.param("pkgd\u00efr/colorsys.py", "pkgdi\u0308r/__init__.py", id="dir-unicode-form"),
    ],
)
def test_deleting_marker_next_to_stdlib_named_file_is_refused(tmp_path, neighbour, marker):
    base = {**BASE, neighbour: BODY, marker: ""}
    reason = _gate(tmp_path, base, {marker: None})
    assert "CANDIDATE_SYS_PATH_MUTATION" in reason
    assert "package marker" in reason


@pytest.mark.parametrize(
    ("base_files", "cand"),
    [
        pytest.param(BASE, {"pkgdir/__init__.py": None}, id="no-stdlib-named-file"),
        pytest.param(
            {**BASE, "pkgdir/colorsys.py": BODY},
            {"pkgdir/m.py": None},
            id="non-marker-delete",
        ),
        pytest.param(
            {**BASE, "pkgdir/sub/colorsys.py": BODY, "pkgdir/sub/__init__.py": ""},
            {"pkgdir/__init__.py": None},
            id="stdlib-name-in-other-dir",
        ),
        pytest.param(
            {**BASE, "pkgdir/colorsys.txt": BODY},
            {"pkgdir/__init__.py": None},
            id="not-a-module-suffix",
        ),
    ],
)
def test_marker_delete_without_exposed_stdlib_name_passes(tmp_path, base_files, cand):
    assert _gate(tmp_path, base_files, cand) == ""


# #1822: a changed (not only added) stdlib-named file where the base makes the directory a root
@pytest.mark.parametrize(
    "base_files",
    [
        pytest.param(
            {**BASE, "pytest.ini": "[pytest]\npythonpath = pkgdir\n", "pkgdir/colorsys.py": BODY},
            id="ini-pythonpath",
        ),
        pytest.param(
            {
                **BASE,
                "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = ['pkgdir']\n",
                "pkgdir/colorsys.py": BODY,
            },
            id="pyproject-pythonpath",
        ),
        pytest.param(
            {
                **BASE,
                "conftest.py": "import sys\nsys.path.insert(0, 'pkgdir')\n",
                "pkgdir/colorsys.py": BODY,
            },
            id="base-conftest-path-edit",
        ),
    ],
)
def test_changed_stdlib_named_file_in_a_base_root_is_refused(tmp_path, base_files):
    reason = _gate(tmp_path, base_files, {"pkgdir/colorsys.py": BODY + "Y = 2\n"})
    assert "CANDIDATE_SYS_PATH_MUTATION" in reason


def test_changed_file_that_does_not_shadow_passes_in_a_base_root(tmp_path):
    base = {**BASE, "pytest.ini": "[pytest]\npythonpath = pkgdir\n"}
    assert _gate(tmp_path, base, {"pkgdir/m.py": BODY + "Y = 2\n"}) == ""


def test_changed_stdlib_named_file_in_a_regular_package_passes(tmp_path):
    base = {**BASE, "pkgdir/colorsys.py": BODY}
    assert _gate(tmp_path, base, {"pkgdir/colorsys.py": BODY + "Y = 2\n"}) == ""


def test_neighbour_lookup_folds_directory_case_and_unicode_form():
    from cairnwork.candidate_stdlib_root import _stdlib_named_in

    assert _stdlib_named_in({"Pkgdir/colorsys.py": "x"}, "pkgdir")
    assert _stdlib_named_in({"pkgdïr/colorsys.py": "x"}, "pkgdïr")
    assert not _stdlib_named_in({"pkgdir/sub/colorsys.py": "x"}, "pkgdir")
