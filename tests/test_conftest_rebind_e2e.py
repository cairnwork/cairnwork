"""#916 end to end: a test-commit conftest that rebinds a base name to weaken a base check.

The base guard test passes on the base and fails on the wrong fix, unless ``STRICT`` is off. The
differential base probe compares base outcomes with and without the test-commit conftest: the guard
passes both ways, so it sees nothing. Before #916 the wrong fix ended COMPLETED; now the static
check refuses the rebinding first. The controls show the guard does its job without the rebinding.
"""

import json
import os
from pathlib import Path

import pytest
from conftest_scenarios import BASE_CONFTEST, PLAIN, conftest_run
from conftest_scenarios import project as _project  # noqa: F401  (the fixture, re-exposed below)
from test_deferred_tests import FIX

pytestmark = pytest.mark.sandbox

GUARDED = {
    "tests/conftest.py": BASE_CONFTEST + "import pytest\n\nSTRICT = True\n\n\n@pytest.fixture\n"
    "def check():\n    def _c(ok):\n        if STRICT:\n            assert ok\n\n    return _c\n",
    "tests/test_guard.py": "from calc import add\n\n\ndef test_guard(check):\n"
    "    check(add(0, 0) < 2)\n",
}
WRONG = {"src/calc.py": "def add(a, b):\n    return 2\n"}  # passes the regression, breaks the guard
NEW_FIXTURE = "\n\n@pytest.fixture\ndef two():\n    return 2\n"
CASES = {  # name: (addition, fix, expected status, a code the reason must name)
    "a rebinding that turns a base check off, wrong fix": (
        "STRICT = False\n", WRONG, "FAILED", "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "a fixture of the same name that checks nothing, wrong fix": (
        "\n\n@pytest.fixture\ndef check():\n    return lambda ok: None\n", WRONG, "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "control: a new fixture, wrong fix": (
        NEW_FIXTURE, WRONG, "FAILED", "p2p-whole-suite: pytest exit code 1",
    ),
    "control: a new fixture, correct fix": (NEW_FIXTURE, FIX, "COMPLETED", ""),
}  # fmt: skip
# r2 (reviewer's counterexample): the base check depends on a builtin fixture, and a nested
# test-commit conftest overrides that builtin for the tests below it
DEPENDS = {
    "tests/conftest.py": BASE_CONFTEST + "import pytest\n\n\n@pytest.fixture\n"
    "def check(monkeypatch):\n    def _c(ok):\n        if monkeypatch is not None:\n"
    "            assert ok\n\n    return _c\n",
    "tests/sub/test_guard.py": "from calc import add\n\n\ndef test_guard(check):\n"
    "    check(add(0, 0) < 2)\n",
}
NESTED = {  # name: (addition to tests/sub/conftest.py, fix, expected status, code)
    "a nested override of a builtin a base fixture above depends on, wrong fix": (
        "import pytest\n\n\n@pytest.fixture\ndef monkeypatch():\n    return None\n", WRONG,
        "FAILED", "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "control: a nested new fixture, wrong fix": (
        "import pytest\n" + NEW_FIXTURE, WRONG, "FAILED", "p2p-whole-suite: pytest exit code 1",
    ),
}  # fmt: skip


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


@pytest.mark.parametrize("name", [*CASES, *NESTED])
def test_a_rebinding_that_weakens_a_base_check_is_refused(project, tmp_path, name):
    nested = name in NESTED
    addition, fix, status, code = (NESTED if nested else CASES)[name]
    result = conftest_run(
        project, tmp_path, addition=addition, regression=PLAIN, fix=fix,
        base_files=DEPENDS if nested else GUARDED, path="tests/sub/conftest.py" if nested
        else "tests/conftest.py",
    )  # fmt: skip
    if table := os.environ.get("SCENARIO_TABLE"):  # the before/after table of the PR body
        row = {"name": name, "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == status, result.reason
    assert code in result.reason
