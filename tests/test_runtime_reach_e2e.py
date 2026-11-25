"""#1769 step 2 (end to end, sandbox): the base collect run's plugin and sys.path record refuses.

Each refusal has a twin that differs only in what the test commit changes; the twin is accepted.
"""

# ruff: noqa: F811  (a pytest fixture is used by its argument name)
from pathlib import Path

import pytest
from conftest import commit_change, git
from conftest_scenarios import BASE_CONFTEST, project  # noqa: F401  (the fixture)
from test_deferred_tests import FIX, SeeingWorker, run

from cairnwork.deferred_data import DATA_TRAILER

pytestmark = pytest.mark.sandbox

REGRESSION = "from calc import add\n\n\ndef test_regression():\n    assert add(1, 1) == 2\n"
HELPER = "# a plugin the base loads by name at run time\n"
BY_NAME = (
    BASE_CONFTEST
    + "import sys\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n\n\n"
    + "def pytest_configure(config):\n    config.pluginmanager.import_plugin('helper_mod')\n"
)
ON_PATH = (
    BASE_CONFTEST + "sys.path.insert(0, str(pathlib.Path(__file__).parent / 'data' / 'lib.py'))\n"
)
ARCHIVE_PATH = (
    "tests/data/lib.py"  # declared data (only .py can be); a base conftest may put it on sys.path
)
ARCHIVE = "x = 1\n"


def _test_commit(project, files, declared):
    base = git(project, "rev-parse", "HEAD")
    root = Path(project).parent
    origin = root / "origin.git"
    if not origin.exists():
        git(root, "clone", "-q", "--bare", str(project), str(origin))
        git(project, "remote", "add", "origin", str(origin))
    work = root / "author-0"
    git(root, "clone", "-q", str(origin), str(work))
    git(work, "config", "user.email", "t@e")
    git(work, "config", "user.name", "t")
    git(work, "checkout", "-q", "--detach", base)
    message = "tests" + (f"\n\n{DATA_TRAILER}: {' '.join(declared)}\n" if declared else "")
    tests = commit_change(work, files, message=message)
    git(work, "push", "-q", "origin", f"{tests}:refs/heads/tests-{tests[:8]}")
    return base, tests


CHECK = ("python3", "-m", "pytest", "-q", "tests/test_regression.py")
OTHER = "tests/test_other.py"  # an unrelated base test file: the small edit every twin also makes
OTHER_TEXT = "def test_other():\n    assert True\n"


def go(project, tmp_path, base_files, test_files, extra=()):
    commit_change(project, {OTHER: OTHER_TEXT, **base_files})
    declared = [p for p in test_files if p == ARCHIVE_PATH]
    base, tests = _test_commit(
        project, {"tests/test_regression.py": REGRESSION, **test_files}, declared
    )
    verifiers = (("pytest", (*CHECK, *extra)),)
    return run(project, tmp_path, base, tests, SeeingWorker(FIX), verifiers=verifiers)


def edit_other(**more):
    """The test commit's small edit to an unrelated base test file (a twin must reach the check)."""
    return {OTHER: OTHER_TEXT + "# edited\n", **more}


def test_a_plugin_loaded_by_import_name_that_the_commit_changes_is_refused(project, tmp_path):
    base = {"tests/conftest.py": BY_NAME, "tests/helper_mod.py": HELPER}
    result = go(project, tmp_path, base, {"tests/helper_mod.py": HELPER + "# changed\n"})
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def test_the_same_plugin_untouched_is_accepted(project, tmp_path):
    base = {"tests/conftest.py": BY_NAME, "tests/helper_mod.py": HELPER}
    result = go(project, tmp_path, base, edit_other(), extra=(OTHER,))
    assert result.status == "COMPLETED", result.reason


def test_an_archive_on_sys_path_that_the_commit_changes_is_refused(project, tmp_path):
    base = {"tests/conftest.py": ON_PATH, ARCHIVE_PATH: ARCHIVE}
    result = go(project, tmp_path, base, {ARCHIVE_PATH: ARCHIVE + "y = 2\n"})
    assert result.status == "FAILED", result.reason
    assert "TEST_COMMIT_CONFTEST_STATIC" in result.reason


def test_the_same_archive_on_sys_path_untouched_is_accepted(project, tmp_path):
    base = {"tests/conftest.py": ON_PATH, ARCHIVE_PATH: ARCHIVE}
    result = go(project, tmp_path, base, edit_other(), extra=(OTHER,))
    assert result.status == "COMPLETED", result.reason
