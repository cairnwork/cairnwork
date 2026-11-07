"""#982 item 4 end to end: a base fixture a conftest above imports from a helper module.

The base guard fixture lives in ``tests/helpers.py`` and depends on the builtin ``monkeypatch``;
``tests/conftest.py`` registers it with ``from helpers import guard``. A nested test-commit
conftest that overrides ``monkeypatch`` turns the guard off for the tests below it. Before #982
item 4 the dependency was read only from the conftest's own text, so the wrong fix ended
COMPLETED; now the helper's requests are read too. The controls show the guard does its job.
"""

import json
import os
from pathlib import Path

import pytest
from conftest_scenarios import BASE_CONFTEST, PLAIN, conftest_run
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)
from test_conftest_rebind_e2e import NEW_FIXTURE, WRONG
from test_deferred_tests import FIX

pytestmark = pytest.mark.sandbox

HELPER = (
    "import pytest\n\n\n@pytest.fixture\ndef guard(monkeypatch):\n    def _c(ok):\n"
    "        if monkeypatch is not None:\n            assert ok\n\n    return _c\n"
)
TEST = "from calc import add\n\n\ndef test_guard(guard):\n    guard(add(0, 0) < 2)\n"
BASES = {  # name: base files; each registers guard in tests/conftest.py from a helper module
    "from helpers import guard": {"tests/helpers.py": HELPER},
    "pytest_plugins = ['helpers']": {"tests/helpers.py": HELPER},
    "from pkg.deep import guard (re-exported)": {
        "tests/pkg/__init__.py": "", "tests/pkg/deep.py": "from pkg.inner import guard\n",
        "tests/pkg/inner.py": HELPER,
    },
}  # fmt: skip
OVERRIDE = "import pytest\n\n\n@pytest.fixture\ndef monkeypatch():\n    return None\n"
CASES = {  # name: (base, addition to tests/sub/conftest.py, fix, expected status, code)
    **{f"{b}: a nested override of what the helper's fixture requests, wrong fix": (
        b, OVERRIDE, WRONG, "FAILED", "TEST_COMMIT_CONFTEST_STATIC") for b in BASES},
    "control: a nested new fixture, wrong fix": (
        "from helpers import guard", "import pytest\n" + NEW_FIXTURE, WRONG, "FAILED",
        "p2p-whole-suite: pytest exit code 1"),
    "control: a nested new fixture, correct fix": (
        "from helpers import guard", "import pytest\n" + NEW_FIXTURE, FIX, "COMPLETED", ""),
}  # fmt: skip


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


@pytest.mark.parametrize("name", CASES)
def test_an_override_of_an_imported_fixtures_dependency_is_refused(project, tmp_path, name):
    base, addition, fix, status, code = CASES[name]
    conftest = BASE_CONFTEST + base.split(" (")[0] + "\n"
    files = {**BASES[base], "tests/conftest.py": conftest, "tests/sub/test_guard.py": TEST}
    result = conftest_run(
        project, tmp_path, addition=addition, regression=PLAIN, fix=fix, base_files=files,
        path="tests/sub/conftest.py",
    )  # fmt: skip
    if table := os.environ.get("SCENARIO_TABLE"):  # the before/after table of the PR body
        row = {"name": name, "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == status, result.reason
    assert code in result.reason
