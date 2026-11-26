"""#1820 second review: large commits through the real sandbox, shapes from the review table.

Must-block rows: a helper that loads an archive the commit changes, however many files are edited
and whether the tree run names them or not. Must-pass rows: ordinary words in a test or helper
(a path, a tmp_path) never refuse a large commit. Each block row has a twin that changes another
archive. Sandbox rows (CAIRNWORK_REQUIRE_SANDBOX=1).
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
import pytest
from conftest_scenarios import project  # noqa: F401  (the fixture)
from test_runtime_reach_cap_e2e import USES_VEND, VEND
from test_runtime_reach_tree_e2e import BROKEN, JOINED, OK, accepted, go, refused

from cairnwork import base_fixture_names

pytestmark = pytest.mark.sandbox

ZIPS = {"tests/m/data/lib.zip": "z", "tests/m/data/other.zip": "o"}
PATHLIB = "from pathlib import Path\n\n\ndef test_h():\n    assert Path\n"


def _run(project, tmp_path, base, *, edit, bad=True, replace=None, delete=()):
    """Edit every ``edit`` file (a trailing comment, or ``replace``); change one archive."""
    files = {p: (replace or {}).get(p, base[p] + "# e\n") for p in edit}
    zip_path = "tests/m/data/lib.zip" if bad else "tests/m/data/other.zip"
    files[zip_path] = "changed"
    return go(
        project, tmp_path, {**base, **ZIPS}, files,
        extra=tuple(sorted(p for p in files if p.endswith(".py"))), delete=delete,
    )  # fmt: skip


def _tests(n, **special):
    return {f"tests/m/test_{i:02d}.py": special.get(f"{i:02d}", OK) for i in range(n)}


def _vend_base(n=21):
    return {**_tests(n, **{"00": BROKEN, "20": OK}), "tests/m/test_zz.py": USES_VEND,
            "tests/m/vend.py": VEND}  # fmt: skip


@pytest.mark.parametrize("bad", [True, False])
def test_row_1_a_broken_first_file_and_many_edits_still_load_the_helper(project, tmp_path, bad):
    base = _vend_base()
    edit = [p for p in base if p.startswith("tests/m/test_")]
    out = _run(project, tmp_path, base, edit=edit, bad=bad, replace={"tests/m/test_00.py": OK})
    (refused if bad else accepted)(out)


@pytest.mark.parametrize("bad", [True, False])
def test_row_3_two_edits_a_broken_one_and_the_helper_user(project, tmp_path, bad):
    base = _vend_base()
    edit = ["tests/m/test_00.py", "tests/m/test_zz.py"]
    out = _run(project, tmp_path, base, edit=edit, bad=bad, replace={"tests/m/test_00.py": OK})
    (refused if bad else accepted)(out)


@pytest.mark.parametrize("helper", [PATHLIB, "def test_h():\n    assert True\n"])
def test_row_4_a_helper_that_merely_names_a_path_does_not_refuse_22_edits(
    project, tmp_path, helper
):
    base = {**_tests(21), "tests/m/zz_helper.py": helper}
    accepted(_run(project, tmp_path, base, edit=list(base), bad=False))


def test_row_6_deleting_files_that_name_a_path_does_not_refuse(project, tmp_path):
    gone = {f"tests/m/gone_{i:02d}.py": "from pathlib import Path\nX = Path\n" for i in range(6)}
    base = {**_tests(15), **gone}
    out = _run(project, tmp_path, base, edit=list(_tests(15)), bad=False, delete=tuple(gone))
    accepted(out)


# rows 7 and 8: a tree over the cap names one file, so the edits are not collected by it
@pytest.mark.parametrize("bad", [True, False])
def test_row_7_the_unnamed_edit_that_loads_the_archive_is_refused(
    project, tmp_path, monkeypatch, bad
):
    monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 0)
    zz = "import sys, pathlib\n" + JOINED + OK
    base = {**_tests(20), "tests/m/test_zz.py": zz}
    out = _run(project, tmp_path, base, edit=list(base), bad=bad)
    (refused if bad else accepted)(out)


def test_row_8_an_unnamed_edit_that_uses_tmp_path_is_accepted(project, tmp_path, monkeypatch):
    monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 0)
    zz = "def test_t(tmp_path):\n    assert tmp_path\n"
    base = {**_tests(20), "tests/m/test_zz.py": zz}
    accepted(_run(project, tmp_path, base, edit=list(base), bad=False))
