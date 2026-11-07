"""#1010 end to end: a base conftest loads a module through ``pytest_plugins``, and the test
commit changes that module (as in #924 r2). Before #1010 only a plain assignment was read, so a
module named by ``append`` or in a comma-separated string was missed and a wrong fix that the
module hides ended COMPLETED. A value built at run time is not followed (#1014 r2): reading it as
every changed module refused every task in such a repository, a correct fix included."""

import json
import os
from pathlib import Path

import pytest
from conftest import commit_change
from conftest_scenarios import BASE_CONFTEST, BREAKS_BASE, NAMES_HELPER, PLAIN, RUNNER_PATCH
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

pytestmark = pytest.mark.sandbox

CASES = {  # name: (base conftest addition, fix, expected status, code)
    "append names the module, wrong fix": (
        'pytest_plugins = []\npytest_plugins.append("colorsys")\n', BREAKS_BASE, "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC"),
    "a comma-separated string names the module, wrong fix": (
        'pytest_plugins = "helpers, colorsys"\n', BREAKS_BASE, "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC"),
    "a list built at run time, correct fix": (
        "PLUGINS = ()\npytest_plugins = [p for p in PLUGINS]\n", FIX, "COMPLETED", ""),
}  # fmt: skip


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


@pytest.mark.parametrize("name", CASES)
def test_a_module_a_base_conftest_loads_as_a_plugin_is_followed(project, tmp_path, name):
    addition, fix, status, code = CASES[name]
    commit_change(project, {"tests/conftest.py": BASE_CONFTEST + addition,
                            "tests/colorsys.py": "def test_helper():\n    pass\n",
                            "tests/helpers.py": ""})  # fmt: skip
    base, tests = make_test_commit(
        project, {"tests/colorsys.py": RUNNER_PATCH, "tests/test_regression.py": PLAIN}
    )
    result = run(project, tmp_path, base, tests, SeeingWorker(fix), key="k1",
                 p2p_mode="whole-suite", p2p_budget=300, verifiers=NAMES_HELPER)  # fmt: skip
    if table := os.environ.get("SCENARIO_TABLE"):  # the before/after table of the PR body
        row = {"name": name, "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == status, result.reason
    assert code in result.reason
