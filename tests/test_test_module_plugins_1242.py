"""#1242: changed test modules cannot load changed pytest plugins."""

import pytest
from conftest import commit_change, git
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)

from cairnwork.conftest_check import check_conftests
from cairnwork.deferred_overlay import TestCommitRejected


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
