"""#1820 review: many edited base files, and a lone broken tree file, through the real sandbox.

Each refusal row has a twin with the same layout and the same edits whose only difference is a
file that is not reached at run time; the twin is accepted.
Sandbox rows (CAIRNWORK_REQUIRE_SANDBOX=1).
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
import pytest
from conftest_scenarios import project  # noqa: F401  (the fixture)
from test_runtime_reach_tree_e2e import (
    BROKEN,
    JOINED,
    OK,
    SIBLING_PLUGIN,
    accepted,
    go,
    refused,
)

from cairnwork import base_fixture_names

pytestmark = pytest.mark.sandbox

VEND = "import sys, pathlib\n" + JOINED + "X = 1\n"
USES_VEND = "import vend\n\n\ndef test_vend():\n    assert vend.X == 1\n"


def _layout(n, gone, kind):
    base = {f"tests/m/test_{i:02d}.py": OK for i in range(n)}
    base.update({f"tests/m/gone_{i:02d}.py": "X = 1\n" for i in range(gone)})
    if kind == "archive":
        base.update(
            {
                "tests/m/vend.py": VEND,
                "tests/m/test_00.py": USES_VEND,  # no signal word in its own text
                "tests/m/data/lib.zip": "z",
                "tests/m/data/other.zip": "o",
            }
        )
    else:
        base.update(
            {
                "tests/m/conftest.py": SIBLING_PLUGIN,
                "tests/lib/helper_mod.py": "# plugin\n",
                "tests/lib/other_mod.py": "# not a plugin\n",
            }
        )
    return base


TARGETS = {  # kind: (the file a bad candidate edits, the twin's file)
    "archive": ("tests/m/data/lib.zip", "tests/m/data/other.zip"),
    "plugin": ("tests/lib/helper_mod.py", "tests/lib/other_mod.py"),
}


def _many(project, tmp_path, n, *, gone=0, kind="archive", bad=True, touch=True):
    base = _layout(n, gone, kind)
    tests = {p: base[p] + "# e\n" for p in base if p.startswith("tests/m/test_")}
    if touch:
        path = TARGETS[kind][0 if bad else 1]
        tests[path] = base[path] + "# edited\n" if path.endswith(".py") else "changed"
    deleted = tuple(p for p in base if "/gone_" in p)
    return go(
        project, tmp_path, base, tests, extra=tuple(sorted(p for p in tests if "test_" in p)),
        delete=deleted,
    )  # fmt: skip


ROWS = {  # name: (edited test files, deleted .py files, kind)
    "21 edits, an archive": (21, 0, "archive"),
    "25 edits, an archive": (25, 0, "archive"),
    "21 edits, a plugin by name": (21, 0, "plugin"),
    "25 edits, a plugin by name": (25, 0, "plugin"),
    "15 edits and 6 deletions": (15, 6, "archive"),
}


@pytest.mark.parametrize("name", ROWS)
def test_many_edited_base_files_do_not_hide_what_an_unedited_helper_loads(project, tmp_path, name):
    n, gone, kind = ROWS[name]
    refused(_many(project, tmp_path, n, gone=gone, kind=kind))


@pytest.mark.parametrize("name", ROWS)
def test_the_same_many_edits_with_an_unreached_file_are_accepted(project, tmp_path, name):
    n, gone, kind = ROWS[name]
    accepted(_many(project, tmp_path, n, gone=gone, kind=kind, bad=False))


def test_many_edited_base_files_beyond_a_tree_over_the_cap_still_load_the_helper(
    project, tmp_path, monkeypatch
):
    monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 0)
    refused(_many(project, tmp_path, 21))


def test_the_same_edits_over_a_tree_over_the_cap_with_an_unreached_file_are_accepted(
    project, tmp_path, monkeypatch
):
    monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 0)
    accepted(_many(project, tmp_path, 21, bad=False))


def test_many_plain_edits_and_no_archive_change_are_accepted(project, tmp_path):
    accepted(_many(project, tmp_path, 21, touch=False))


def test_exactly_the_cap_of_edits_is_refused_and_its_twin_accepted(project, tmp_path):
    n = base_fixture_names.MAX_TARGETS
    refused(_many(project, tmp_path, n))


def test_exactly_the_cap_of_edits_with_an_unreached_file_is_accepted(project, tmp_path):
    accepted(_many(project, tmp_path, base_fixture_names.MAX_TARGETS, bad=False))


# #1809 (b): the issue's shape: the only tree file is broken, and no conftest directory is left
ROOT_CONFTEST = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'tests' / 'data' / ('lib' + '.zip')))\n"
)


def _lone(project, tmp_path, where, *, bad):
    base = {
        "conftest.py": ROOT_CONFTEST,
        "tests/data/lib.zip": "z",
        "tests/data/other.zip": "o",
        where: BROKEN,
    }
    fixed = {("tests/data/lib.zip" if bad else "tests/data/other.zip"): "z2", where: OK}
    return go(
        project, tmp_path, base, fixed, extra=(where,),
        base_delete=("tests/conftest.py", "tests/test_base.py"),
    )  # fmt: skip


LONE_WHERE = ["tests/test_only.py", "tests/sub/test_only.py"]


@pytest.mark.parametrize("where", LONE_WHERE)
def test_a_lone_broken_tree_file_with_no_conftest_directory_still_loads_the_root_conftest(
    project, tmp_path, where
):
    refused(_lone(project, tmp_path, where, bad=True))


@pytest.mark.parametrize("where", LONE_WHERE)
def test_the_same_lone_broken_tree_file_with_an_unreached_archive_is_accepted(
    project, tmp_path, where
):
    accepted(_lone(project, tmp_path, where, bad=False))
