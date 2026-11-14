"""#1242: changed test modules cannot load changed pytest plugins."""

import pytest
from conftest import commit_change, git
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

from cairnwork.conftest_check import check_conftests
from cairnwork.deferred_overlay import TestCommitRejected
from cairnwork.module_plugins import check_test_module_plugins


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _commit(project, files):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, files)
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    return base, commit


@pytest.mark.parametrize("plugins", ['"tests.plug"', '["tests.plug"]', '("tests.plug",)'])
def test_changed_plugin_module_is_refused_for_each_literal_form(project, plugins):
    base, commit = _commit(
        project,
        {
            "tests/test_case.py": f"pytest_plugins = {plugins}\ndef test_case(): pass\n",
            "tests/plug.py": "def pytest_collection_modifyitems(items): pass\n",
        },
    )
    with pytest.raises(TestCommitRejected) as caught:
        check_conftests(project, base, commit, ["tests/test_case.py", "tests/plug.py"])
    assert caught.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "tests/test_case.py" in str(caught.value)


@pytest.mark.parametrize(
    "plugin",
    [
        "def pytest_collection_modifyitems(items): pass\n",
        "import pytest\n@pytest.fixture\ndef helper(): return 1\n",
    ],
)
def test_changed_plugin_hooks_or_fixtures_are_refused(project, plugin):
    base, commit = _commit(
        project,
        {
            "tests/test_case.py": 'pytest_plugins = "tests.plug"\n',
            "tests/plug.py": plugin,
        },
    )
    with pytest.raises(TestCommitRejected):
        check_conftests(project, base, commit, ["tests/test_case.py", "tests/plug.py"])


def test_changed_parent_package_init_is_refused(project):
    base, commit = _commit(
        project,
        {
            "tests/test_case.py": 'pytest_plugins = ["pkg.plug"]\n',
            "tests/pkg/__init__.py": "VALUE = 1\n",
            "tests/pkg/plug.py": "VALUE = 2\n",
        },
    )
    with pytest.raises(TestCommitRejected):
        check_conftests(
            project,
            base,
            commit,
            [
                "tests/test_case.py",
                "tests/pkg/__init__.py",
                "tests/pkg/plug.py",
            ],
        )


@pytest.mark.parametrize("value", ["PLUGINS", "make_plugins()"])
def test_nonliteral_plugin_value_is_refused(project, value):
    base, commit = _commit(project, {"tests/test_case.py": f"pytest_plugins = {value}\n"})
    with pytest.raises(TestCommitRejected) as caught:
        check_conftests(project, base, commit, ["tests/test_case.py"])
    assert caught.value.code == "TEST_COMMIT_CONFTEST_STATIC"


@pytest.mark.parametrize(
    "source",
    [
        'pytest_plugins = "json"\n',
        'pytest_plugins = "pytest_asyncio"\n',
        "def test_case(): pass\n",
        'def test_case():\n    pytest_plugins = "tests.plug"\n',
    ],
)
def test_unchanged_or_absent_plugin_declarations_pass(project, source):
    base, commit = _commit(project, {"tests/test_case.py": source})
    check_conftests(project, base, commit, ["tests/test_case.py"])


def test_unchanged_repo_plugin_module_is_accepted(project):
    commit_change(project, {"tests/helpers.py": "VALUE = 1\n"})
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, {"tests/test_case.py": 'pytest_plugins = "tests.helpers"\n'})
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    check_conftests(project, base, commit, ["tests/test_case.py"])


@pytest.mark.parametrize(
    "source",
    [
        'pytest_plugins = "json,plug"\n',
        'pytest_plugins = []\npytest_plugins.append("plug")\n',
        'pytest_plugins = []\npytest_plugins.extend(["plug"])\n',
        'pytest_plugins = []\npytest_plugins.insert(0, "plug")\n',
        '(pytest_plugins := "plug")\n',
        'try:\n raise KeyError\nexcept KeyError:\n pytest_plugins = "plug"\n',
        'match 1:\n case _:\n  pytest_plugins = "plug"\n',
        'for pytest_plugins in ["plug"]:\n pass\n',
        'def _f():\n global pytest_plugins\n pytest_plugins = "plug"\n_f()\n',
        'pytest_plugins = "zzpkg.plug"\n',
        "from helper import P as pytest_plugins\n",
        "import pytest_plugins\n",
        'pytest_plugins = []\npytest_plugins += ["plug"]\n',
        'pytest_plugins = []\npytest_plugins[0:0] = ["plug"]\n',
        'pytest_plugins, other = "plug", 1\n',
        "pytest_plugins = PLUGINS\n",
        'pytest_plugins = []\ndef _f():\n pytest_plugins.append("plug")\n_f()\n',
        'pytest_plugins = []\nclass C:\n pytest_plugins.append("plug")\n',
        'pytest_plugins = []\nalias = pytest_plugins\nalias.append("plug")\n',
        "try:\n pass\nexcept Exception as pytest_plugins:\n pass\n",
        'match ["plug"]:\n case pytest_plugins:\n  pass\n',
        "with open(__file__) as pytest_plugins:\n pass\n",
        '\uff50ytest_plugins = "plug"\n',
        '[(pytest_plugins := "plug") for _ in "x"]\n',
        'def _f(x=(pytest_plugins := "plug")):\n pass\n',
        'pytest_plugins = []\nclass C(pytest_plugins.append("plug") or object):\n pass\n',
        'pytest_plugins = []\nf = lambda: pytest_plugins.append("plug")\nf()\n',
        'pytest_plugins = []\nclass C:\n pytest_plugins.append("plug")\n pytest_plugins = 1\n',
        'pytest_plugins = []\npytest_plugins += ["json"]\n',
        'pytest_plugins = []\npytest_plugins[0:0] = ["json"]\n',
        'pytest_plugins = x = pytest_plugins = "json"\n',
        "match {}:\n case {**pytest_plugins}:\n  pass\n",
        "pytest_plugins = None\n",
        'pytest_plugins = ["json"] + EXTRA\n',
        'pytest_plugins = "json" + EXTRA\n',
    ],
)
def test_module_binding_routes_to_changed_plugin_are_refused(project, source):
    base, commit = _commit(
        project,
        {
            "tests/test_case.py": source,
            "tests/plug.py": "def pytest_collection_modifyitems(items): pass\n",
            "tests/zzpkg.py": "VALUE = 1\n",
            "tests/helper.py": 'P = "plug"\n',
        },
    )
    paths = ["tests/test_case.py", "tests/plug.py", "tests/zzpkg.py", "tests/helper.py"]
    with pytest.raises(TestCommitRejected) as caught:
        check_conftests(project, base, commit, paths)
    assert caught.value.code == "TEST_COMMIT_CONFTEST_STATIC"


HOOK = "def pytest_collection_modifyitems(items): pass\n"


@pytest.mark.parametrize(
    "files,paths",
    [
        (
            {
                "tests/agg.py": "from plug import *\n",
                "tests/plug.py": HOOK,
                "tests/test_case.py": 'pytest_plugins = "agg"\n',
            },
            ["tests/plug.py"],
        ),
        (
            {
                "tests/agg.py": 'pytest_plugins = "plug"\n',
                "tests/plug.py": HOOK,
                "tests/test_case.py": 'pytest_plugins = "agg"\n',
            },
            ["tests/plug.py"],
        ),
        (
            {
                "tests/plug.py": HOOK,
                "tests/test_old.py": 'pytest_plugins = "plug"\n',
                "tests/test_new.py": "def test_new(): pass\n",
            },
            ["tests/plug.py", "tests/test_new.py"],
        ),
        (
            {
                "tests/plug.py": HOOK,
                "tests/helper.py": 'pytest_plugins = "plug"\n',
                "tests/test_case.py": "from helper import *\n",
            },
            ["tests/plug.py", "tests/test_case.py"],
        ),
    ],
)
def test_plugin_reachability_through_tree_is_refused(project, files, paths):
    base, commit = _commit(project, files)
    with pytest.raises(TestCommitRejected):
        check_conftests(project, base, commit, paths)


@pytest.mark.parametrize(
    "source",
    [
        'pytest_plugins = "json"\n',
        'pytest_plugins = ["pytester"]\n',
        'pytest_plugins = "pytest_asyncio"\n',
        'pytest_plugins = "json,helpers"\n',
        'pytest_plugins = "helpers"\n',
        'pytest_plugins = "hel" + "pers"\n',
        'def test_case():\n pytest_plugins = "plug"\n',
        'def test_case(pytest_plugins="plug"):\n return pytest_plugins\n',
        'class C:\n pytest_plugins = "plug"\n',
        "pytest_plugins: list\n",
        "import plug\n",
        "from helpers import *\n",
    ],
)
def test_unrelated_or_non_plugin_bindings_are_accepted(project, source):
    commit_change(project, {"tests/helpers.py": 'VALUE = 1\npytest_plugins = "json"\n'})
    base, commit = _commit(
        project,
        {
            "tests/test_case.py": source,
            "tests/plug.py": "def pytest_collection_modifyitems(items): pass\n",
            "tests/other.py": "VALUE = 1\n",
        },
    )
    paths = ["tests/test_case.py", "tests/plug.py", "tests/other.py"]
    check_conftests(project, base, commit, paths)


def test_a_declared_data_file_loaded_as_a_plugin_is_refused(project):
    base = git(project, "rev-parse", "HEAD")
    commit_change(
        project,
        {
            "tests/data/x.py": "def pytest_collection_modifyitems(items): pass\n",
            "tests/test_case.py": 'pytest_plugins = "data.x"\n',
        },
    )
    git(project, "commit", "--amend", "-m", "data\n\nCairnwork-Data: tests/data/x.py")
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    with pytest.raises(TestCommitRejected):
        check_conftests(project, base, commit, ["tests/data/x.py", "tests/test_case.py"])


def test_declared_unparsable_data_file_is_not_parsed(project):
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, {"tests/data/x.py": "def broken(:\n"})
    git(project, "commit", "--amend", "-m", "data\n\nCairnwork-Data: tests/data/x.py")
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    check_conftests(project, base, commit, ["tests/data/x.py"])


def test_a_plugin_name_spelled_in_another_source_encoding_is_refused(project):
    base = git(project, "rev-parse", "HEAD")
    source = '# coding: shift_jis\n\uff50ytest_plugins = "plug"\n'.encode("shift_jis")
    (project / "tests").mkdir(exist_ok=True)
    (project / "tests/test_case.py").write_bytes(source)
    commit = commit_change(project, {"tests/plug.py": HOOK})
    git(project, "reset", "-q", "--hard", base)
    with pytest.raises(TestCommitRejected):
        check_conftests(project, base, commit, ["tests/test_case.py", "tests/plug.py"])


def test_an_unchanged_conftest_is_left_to_the_conftest_check(project):
    commit_change(
        project,
        {"tests/conftest.py": 'pytest_plugins = "json"\ndef f():\n    return pytest_plugins\n'},
    )
    base, commit = _commit(project, {"tests/other.py": "VALUE = 1\n"})
    check_conftests(project, base, commit, ["tests/other.py"])


def test_a_changed_module_that_does_not_parse_here_is_refused(project):
    source = 'pytest_plugins = "plug"\ndef broken(:\n'
    base, commit = _commit(project, {"tests/test_case.py": source, "tests/plug.py": HOOK})
    with pytest.raises(TestCommitRejected) as caught:
        check_conftests(project, base, commit, ["tests/test_case.py", "tests/plug.py"])
    assert caught.value.code == "TEST_COMMIT_UNPARSABLE"


def test_declared_or_unchanged_unparsable_candidates_are_skipped(project):
    commit_change(project, {"tests/test_old.py": "# caf\u00e9\ndef broken(:\n"})
    base = git(project, "rev-parse", "HEAD")
    commit_change(project, {"tests/data/x.py": "# caf\u00e9\ndef broken(:\n"})
    git(project, "commit", "--amend", "-m", "data\n\nCairnwork-Data: tests/data/x.py")
    commit = git(project, "rev-parse", "HEAD")
    git(project, "reset", "-q", "--hard", base)
    check_conftests(project, base, commit, ["tests/data/x.py"])


@pytest.mark.parametrize(
    "new",
    [
        {},
        {"tests/test_u.py": "# caf\u00e9\ndef test_case(): pass\n"},
        {"tests/test_u.py": "# pytest_plugins not used\ndef test_case(): pass\n"},
        {"tests/test_u.py": 'pytest_plugins = "json"\ndef test_case(): pass\n'},
    ],
)
def test_a_changed_package_init_is_not_a_plugin_of_the_tests_inside_it(project, new):
    commit_change(
        project,
        {"tests/__init__.py": "", "tests/test_old.py": "# caf\u00e9\ndef test_case(): pass\n"},
    )
    _, commit = _commit(project, {"tests/__init__.py": "VALUE = 1\n", **new})
    check_test_module_plugins(project, commit, ("tests/__init__.py", *new))
