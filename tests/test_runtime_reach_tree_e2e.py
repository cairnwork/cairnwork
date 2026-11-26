"""#1769 review: the base files the reach run collects do not depend on what the commit changes.

Each refusal row has a twin with the same layout and the same extra edit, which changes a base file
that is not reached at run time; the twin is accepted. Sandbox rows: CAIRNWORK_REQUIRE_SANDBOX=1.
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
import os

import pytest
from conftest import commit_change
from conftest_scenarios import BASE_CONFTEST, project  # noqa: F401  (the fixture)
from test_deferred_tests import FIX, SeeingWorker, run
from test_runtime_reach_e2e import CHECK, REGRESSION, _test_commit

pytestmark = pytest.mark.sandbox

OK = "def test_ok():\n    assert True\n"
JOINED = "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'data' / ('lib' + '.zip')))\n"
SIBLING_ZIP = "import sys, pathlib\n" + JOINED
SIBLING_PLUGIN = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'lib'))\n\n\n"
    "def pytest_configure(config):\n    config.pluginmanager.import_plugin('helper_mod')\n"
)
ZIPS = {"tests/a/data/lib.zip": "z", "tests/a/data/other.zip": "o"}
SIBLING = {"tests/a/conftest.py": SIBLING_ZIP, "tests/a/test_a.py": OK, "tests/z/test_z.py": OK}
CHAIN = {"tests/conftest.py": BASE_CONFTEST + JOINED, "tests/data/lib.zip": "z",
         "tests/data/other.zip": "o"}  # fmt: skip
PLUGIN = {
    "tests/b/conftest.py": SIBLING_PLUGIN,
    "tests/b/test_b.py": OK,
    "tests/lib/helper_mod.py": "# plugin\n" + OK,
    "tests/lib/other_mod.py": "# not a plugin\n" + OK,
}
BROKEN = "import no_such_module_for_the_reach_run\n\n\n" + OK


def go(project, tmp_path, base_files, test_files, extra=(), delete=(), base_delete=()):
    commit_change(project, base_files, delete=base_delete)
    files = {"tests/test_regression.py": REGRESSION, **test_files}
    base, tests = _test_commit(project, files, [], delete=delete)
    verifiers = (("pytest", (*CHECK, *extra)),)
    return run(project, tmp_path, base, tests, SeeingWorker(FIX), verifiers=verifiers)


def edited(path, text=OK):
    return {path: text + "# edited\n"}


def refused(result):
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def accepted(result):
    assert result.status == "COMPLETED", result.reason


Z = "tests/z/test_z.py"
LAYOUTS = {  # name: (base files, the file the commit edits, the twin's file, extra argv)
    "a sibling conftest joins a zip name": (
        {**SIBLING, **ZIPS}, "tests/a/data/lib.zip", "tests/a/data/other.zip"
    ),
    "the same chain's conftest": ({**CHAIN}, "tests/data/lib.zip", "tests/data/other.zip"),
}  # fmt: skip


@pytest.mark.parametrize("name", LAYOUTS)
def test_an_archive_a_sibling_or_chain_conftest_puts_on_sys_path_is_refused_beside_a_base_test_edit(
    project, tmp_path, name
):
    base, target, _ = LAYOUTS[name]
    base = {**base, Z: OK}
    result = go(project, tmp_path, base, {target: "z2", Z: OK + "# e\n"}, extra=(Z,))
    refused(result)


@pytest.mark.parametrize("name", LAYOUTS)
def test_the_same_layout_with_an_archive_off_sys_path_is_accepted(project, tmp_path, name):
    base, _, twin = LAYOUTS[name]
    base = {**base, Z: OK}
    result = go(project, tmp_path, base, {twin: "o2", Z: OK + "# e\n"}, extra=(Z,))
    accepted(result)


@pytest.mark.parametrize("name", LAYOUTS)
def test_a_broken_base_test_the_commit_fixes_does_not_hide_the_archive(project, tmp_path, name):
    base, target, _ = LAYOUTS[name]
    base = {**base, "tests/test_broken.py": BROKEN}
    fixed = {target: "z2", "tests/test_broken.py": OK}
    result = go(project, tmp_path, base, fixed, extra=("tests/test_broken.py",))
    refused(result)


@pytest.mark.parametrize("name", LAYOUTS)
def test_the_same_broken_base_test_with_an_archive_off_sys_path_is_accepted(
    project, tmp_path, name
):
    """Layer1 ruling 2: the dropped broken file shows no sys.path or plugin signal, so it passes."""
    base, _, twin = LAYOUTS[name]
    base = {**base, "tests/test_broken.py": BROKEN}
    result = go(
        project, tmp_path, base, {twin: "o2", "tests/test_broken.py": OK},
        extra=("tests/test_broken.py",),
    )  # fmt: skip
    accepted(result)


def test_a_plugin_a_sibling_conftest_registers_by_name_is_refused_when_the_commit_changes_it(
    project, tmp_path
):
    result = go(
        project, tmp_path, PLUGIN, edited("tests/lib/helper_mod.py", "# plugin\n" + OK),
        extra=("tests/b/test_b.py", "tests/lib/helper_mod.py"),
    )  # fmt: skip
    refused(result)


def test_a_module_beside_that_plugin_that_is_not_a_plugin_is_accepted(project, tmp_path):
    result = go(
        project, tmp_path, PLUGIN, edited("tests/lib/other_mod.py", "# not a plugin\n" + OK),
        extra=("tests/b/test_b.py", "tests/lib/other_mod.py"),
    )  # fmt: skip
    accepted(result)


# review round 2: a symlink, a case variant or a path inside the archive reaches the same file
def _entry(expr):
    return (
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent / "
        + expr
        + "))\n"
    )


REACH = {  # name: (the conftest's path expression, symlinks to make: link -> target)
    "a symlink": ("'data' / 'link.zip'", (("tests/a/data/link.zip", "lib.zip"),)),
    "a case variant": ("'data' / 'LIB.zip'", ()),
    "a path inside the archive": ("'data' / ('lib' + '.zip') / 'pkg'", ()),
}


def _reach_go(project, tmp_path, name, test_files):
    expr, links = REACH[name]
    for link, target in links:
        (project / link).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, project / link)
    base = {**SIBLING, **ZIPS, "tests/a/conftest.py": _entry(expr), Z: OK}
    return go(project, tmp_path, base, {**test_files, Z: OK + "# e\n"}, extra=(Z,))


@pytest.mark.parametrize("name", REACH)
def test_an_archive_reached_by_a_symlink_a_case_variant_or_a_path_inside_it_is_refused(
    project, tmp_path, name
):
    refused(_reach_go(project, tmp_path, name, {"tests/a/data/lib.zip": "z2"}))


@pytest.mark.parametrize("name", REACH)
def test_the_same_symlink_case_or_inside_layouts_with_an_archive_off_sys_path_are_accepted(
    project, tmp_path, name
):
    accepted(_reach_go(project, tmp_path, name, {"tests/a/data/other.zip": "o2"}))


# layer1 ruling 2: a base over the whole-tree cap (cap forced to 1 here) is not mass-refused
@pytest.fixture
def over_cap(monkeypatch):
    from cairnwork import base_fixture_names

    monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 1)


def test_a_base_over_the_cap_accepts_an_honest_edit_of_a_base_test_file(
    project, tmp_path, over_cap
):
    base = {**SIBLING, **ZIPS, Z: OK, "tests/y/test_y.py": OK}
    accepted(go(project, tmp_path, base, {Z: OK + "# e\n"}, extra=(Z,)))


def test_a_base_over_the_cap_still_refuses_an_archive_a_sibling_conftest_puts_on_sys_path(
    project, tmp_path, over_cap
):
    base = {**SIBLING, **ZIPS, Z: OK, "tests/y/test_y.py": OK}
    refused(
        go(project, tmp_path, base, {"tests/a/data/lib.zip": "z2", Z: OK + "# e\n"}, extra=(Z,))
    )


# layer1 round 3: shapes the first over-cap fix did not close (cap forced to 1 for the "over" rows)
@pytest.fixture(params=["whole", "over"])
def either_cap(request, monkeypatch):
    from cairnwork import base_fixture_names

    if request.param == "over":
        monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 1)


ONLY = {"tests/a/conftest.py": SIBLING_ZIP, "tests/a/test_broken.py": BROKEN, Z: OK, **ZIPS}


def test_a_dropped_broken_test_that_was_the_only_one_below_its_conftest_still_loads_it(
    project, tmp_path, either_cap
):
    fixed = {"tests/a/data/lib.zip": "z2", "tests/a/test_broken.py": OK}
    refused(go(project, tmp_path, ONLY, fixed, extra=("tests/a/test_broken.py",)))


def test_the_same_only_broken_test_with_an_archive_off_sys_path_is_accepted(project, tmp_path):
    fixed = {"tests/a/data/other.zip": "o2", "tests/a/test_broken.py": OK}
    accepted(go(project, tmp_path, ONLY, fixed, extra=("tests/a/test_broken.py",)))


NOTEST = {"tests/a/conftest.py": SIBLING_ZIP, Z: OK, **ZIPS}


def test_a_conftest_directory_with_no_base_test_still_loads_when_the_commit_adds_one(
    project, tmp_path, either_cap
):
    new = {"tests/a/test_new.py": OK, "tests/a/data/lib.zip": "z2"}
    refused(go(project, tmp_path, NOTEST, new, extra=("tests/a/test_new.py",)))


def test_the_same_directory_with_an_archive_off_sys_path_is_accepted(project, tmp_path):
    new = {"tests/a/test_new.py": OK, "tests/a/data/other.zip": "o2"}
    accepted(go(project, tmp_path, NOTEST, new, extra=("tests/a/test_new.py",)))


ROOT = {
    "conftest.py": "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'tests' / 'data' / 'lib.zip'))\n",
    "tests/data/lib.zip": "z",
    "tests/data/other.zip": "o",
    "tests/x/test_x.py": OK,
    "tests/y/test_y.py": OK,
}  # fmt: skip


def test_a_root_conftest_that_puts_an_archive_on_sys_path_loads_over_the_cap(
    project, tmp_path, over_cap
):
    refused(go(project, tmp_path, ROOT, {"tests/data/lib.zip": "z2"}))


def test_the_same_root_conftest_layout_with_an_archive_off_sys_path_is_accepted(
    project, tmp_path, over_cap
):
    accepted(go(project, tmp_path, ROOT, {"tests/data/other.zip": "o2"}))


# #1809 (b): the only tree file is broken and dropped, and no conftest directory is under the roots
LONE = {
    "conftest.py": ROOT["conftest.py"],
    "tests/data/lib.zip": "z",
    "tests/data/other.zip": "o",
    "tests/test_only.py": BROKEN,
}


@pytest.mark.parametrize("cap", ["whole", "over"])
def test_the_only_broken_tree_file_dropped_still_loads_the_root_conftest(
    project, tmp_path, monkeypatch, cap
):
    if cap == "over":
        from cairnwork import base_fixture_names

        monkeypatch.setattr(base_fixture_names, "MAX_TREE_TARGETS", 0)
    fixed = {"tests/data/lib.zip": "z2", "tests/test_only.py": OK}
    refused(go(project, tmp_path, LONE, fixed, extra=("tests/test_only.py",)))


def test_the_same_lone_broken_file_with_an_archive_off_sys_path_is_accepted(project, tmp_path):
    fixed = {"tests/data/other.zip": "o2", "tests/test_only.py": OK}
    accepted(go(project, tmp_path, LONE, fixed, extra=("tests/test_only.py",)))


# #1809: a deleted base archive on sys.path is a change the reach check sees
def test_a_deleted_archive_a_conftest_puts_on_sys_path_is_refused(project, tmp_path):
    base = {**SIBLING, **ZIPS, Z: OK}
    gone = ("tests/a/data/lib.zip",)
    refused(go(project, tmp_path, base, {Z: OK + "# e\n"}, extra=(Z,), delete=gone))


def test_a_deleted_archive_off_sys_path_is_accepted(project, tmp_path):
    base = {**SIBLING, **ZIPS, Z: OK}
    gone = ("tests/a/data/other.zip",)
    accepted(go(project, tmp_path, base, {Z: OK + "# e\n"}, extra=(Z,), delete=gone))
