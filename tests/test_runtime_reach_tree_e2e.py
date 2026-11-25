"""#1769 review: the base files the reach run collects do not depend on what the commit changes.

Each refusal row has a twin with the same layout and the same extra edit, which changes a base file
that is not reached at run time; the twin is accepted. Sandbox rows: CAIRNWORK_REQUIRE_SANDBOX=1.
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
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


def go(project, tmp_path, base_files, test_files, extra=()):
    commit_change(project, base_files)
    base, tests = _test_commit(project, {"tests/test_regression.py": REGRESSION, **test_files}, [])
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
