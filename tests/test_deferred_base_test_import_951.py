"""#951 (residual of cw#948 / #924): the base-test-file variant of
``test_a_base_conftest_that_imports_a_changed_module_is_refused``. The base conftest imports
nothing; a base *test file* imports the module the test commit changes. cw#948 looks only at
conftests, so this ends COMPLETED. The test pins that present behaviour (the open residual); the
wanted outcome is the xfail(strict) twin below, which flips to a failure the day a rule lands."""

import pytest
from conftest import commit_change
from conftest_scenarios import (
    BASE_CONFTEST,
    BREAKS_BASE,
    NAMES_HELPER,
    PLAIN,
    RUNNER_PATCH,
)
from conftest_scenarios import (
    project as _project,  # noqa: F401  (the fixture, re-exposed below)
)
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

pytestmark = pytest.mark.sandbox

BASE_IMPORTER = "import colorsys\n\n\ndef test_uses_helper():\n    assert colorsys is not None\n"


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


def _go(project, tmp_path, imported):
    importer = {"tests/test_importer.py": BASE_IMPORTER} if imported else {}
    commit_change(project, {"tests/colorsys.py": "def test_helper():\n    pass\n", **importer})
    base, tests = make_test_commit(
        project, {"tests/colorsys.py": RUNNER_PATCH, "tests/test_regression.py": PLAIN}
    )
    fix = BREAKS_BASE if imported else FIX
    return run(project, tmp_path, base, tests, SeeingWorker(fix), key="k1",
               p2p_mode="whole-suite", p2p_budget=300, verifiers=NAMES_HELPER)  # fmt: skip


def test_the_base_conftest_here_imports_nothing():
    assert "import" in BASE_CONFTEST and "colorsys" not in BASE_CONFTEST


@pytest.mark.parametrize("imported", [True, False])
def test_a_base_test_file_import_of_a_changed_module_is_not_refused_today(
    project, tmp_path, imported
):
    """Residual pinned: a base test file imports the changed module; no conftest does."""
    result = _go(project, tmp_path, imported)
    assert "TEST_COMMIT_CONFTEST_STATIC" not in result.reason
    assert result.status == "COMPLETED", result.reason


@pytest.mark.xfail(strict=True, reason="#951: only base test files import the changed module")
def test_a_base_test_file_import_of_a_changed_module_should_be_refused(project, tmp_path):
    result = _go(project, tmp_path, True)
    assert result.status == "FAILED", result.reason
