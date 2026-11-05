"""The scenarios of #798 half 2 (tests/test_deferred_conftest.py): a test commit that adds a
fixture to a conftest.py. A reviewer builds a counterexample here: change the ``addition`` (the
text appended to the base conftest), the ``regression`` (the new test) or ``base_files`` (the
base) of a ``SCENARIOS`` entry, or call ``conftest_run`` directly. It lays that conftest in a
real test commit and runs the whole pipeline (static check, differential base probe, verifiers).
No import of the conftest checks, so the same scenarios run on a tree without them (the before
column)."""

import pytest
from conftest import commit_change, git
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

pytestmark = pytest.mark.sandbox

CALC = "def add(a, b):\n    return a + b + 1\n"
BASE_CONFTEST = (
    "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
)
BASE_TEST = "from calc import add\ndef test_base_add():\n    assert add(2, 3)\n"
FIXTURE = "\nimport pytest\n\n\n@pytest.fixture\ndef two():\n    return 2\n"
USES_FIXTURE = "from calc import add\ndef test_regression(two):\n    assert add(1, 1) == two\n"
PLAIN = "from calc import add\ndef test_regression():\n    assert add(1, 1) == 2\n"
# a base fixture one directory up that a base test below reads; a nested conftest could shadow it
SHADOWED = {
    "tests/conftest.py": BASE_CONFTEST + "import pytest\n\n\n@pytest.fixture\ndef val():\n"
    "    return 1\n",
    "tests/sub/test_val.py": "def test_val(val):\n    assert val == 1\n",
}
# an import-time effect of a repository module: it reaches every test of the process, not only the
# files under the conftest that imports it (two files outside tests/sub, the affected one second)
SIDEFX = {
    "src/sidefx.py": "import os\nos.environ['W3B_FLAG'] = '1'\n",
    "tests/other/test_a.py": "def test_a():\n    assert True\n",
    "tests/other/test_env.py": "import os\n\n\ndef test_env():\n"
    "    assert 'W3B_FLAG' not in os.environ\n",
    "tests/sub/test_x.py": "def test_x():\n    assert True\n",
}
SLOW = "import time\n\n\ndef test_slow():\n    time.sleep(60)\n"
LEAK = (  # a fixture that leaves process state; a new test of a base file uses it (CE1)
    "\nimport os\nimport pytest\n\n\n@pytest.fixture\ndef leak():\n"
    "    os.environ['W3B_FLAG'] = '1'\n"
)
USES_LEAK = "def test_a():\n    assert True\n\n\ndef test_new(leak):\n    assert True\n"
BOTH = (
    (
        "pytest",
        ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/other/test_a.py"),
    ),
)  # a test commit that edits a base file names it
SLOW_NEW = (
    "def test_a():\n    assert True\n\n\ndef test_new():\n    import time\n    time.sleep(60)\n"
)
MODULE_LEVEL = (  # a module-level change in a base file: all its base tests are the author's
    "import os\nos.environ['W3B_FLAG'] = '1'\n\n\ndef test_env():\n"
    "    assert 'W3B_FLAG' not in os.environ\n\n\ndef test_new():\n    assert True\n"
)
NEW_ONLY = (
    (
        "pytest",
        (
            "python3",
            "-m",
            "pytest",
            "-q",
            "tests/test_regression.py",
            "tests/other/test_env.py::test_new",
        ),
    ),
)
NAMES_MODULE = (  # the verifier names the new test and the module beside the conftest (CE4)
    (
        "pytest",
        ("python3", "-m", "pytest", "-q", "tests/test_regression.py", "tests/sub/colorsys.py"),
    ),
)
SHADOW = "import pytest\n\n\n@pytest.fixture\ndef val():\n    return 2\n"
# a test-commit module beside a new conftest, named like a standard module the conftest imports;
# importing it flips a base test in the same directory
LOCAL_MODULE = {
    "tests/sub/test_env.py": "import os\n\n\ndef test_env():\n"
    "    assert 'W3B_FLAG' not in os.environ\n",
}


@pytest.fixture
def project(repo):
    commit_change(repo, {"src/calc.py": CALC, "tests/conftest.py": BASE_CONFTEST,
                         "tests/test_base.py": BASE_TEST})  # fmt: skip
    return repo


def conftest_run(
    project,
    tmp_path,
    *,
    addition=FIXTURE,
    regression=USES_FIXTURE,
    path="tests/conftest.py",
    base_files=None,
    mode="whole-suite",
    budget=300,
    extra_files=None,
    verifiers=None,
    key="k1",
):
    """One task whose test commit adds ``addition`` to the base conftest at ``path`` (or creates
    the file when the base has none there) next to the new test ``regression``."""
    if base_files:
        commit_change(project, base_files)
    base_text = git(project, "show", f"HEAD:{path}") if _has(project, path) else ""
    files = {
        path: base_text + "\n" + addition,
        "tests/test_regression.py": regression,
        **(extra_files or {}),
    }
    base, tests = make_test_commit(project, files)
    return run(
        project,
        tmp_path,
        base,
        tests,
        SeeingWorker(FIX),
        key=key,
        p2p_mode=mode,
        p2p_budget=budget,
        **({"verifiers": verifiers} if verifiers else {}),
    )


def _has(project, path):
    return bool(git(project, "ls-tree", "HEAD", "--", path))


SCENARIOS = {  # name: (kwargs of conftest_run, expected status, a code the reason holds)
    "fixture-only conftest and a test using it": ({}, "COMPLETED", ""),
    "a hook that deselects": (
        {"addition": "def pytest_collection_modifyitems(items):\n    items.clear()\n"},
        "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "a collect_ignore list": (
        {"addition": "collect_ignore = ['test_base.py']\n"},
        "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "a plugins list": (
        {"addition": "pytest_plugins = ['pytester']\n"},
        "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "an autouse fixture": (
        {"addition": FIXTURE.replace("fixture", "fixture(autouse=True)", 1)},
        "FAILED",
        "TEST_COMMIT_CONFTEST_STATIC",
    ),
    "a fixture override that flips a base test": (
        {
            "addition": SHADOW,
            "path": "tests/sub/conftest.py",
            "base_files": SHADOWED,
            "regression": PLAIN,
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "an import side effect on a test outside its directory": (
        {
            "addition": "import sidefx\n",
            "path": "tests/sub/conftest.py",
            "base_files": SIDEFX,
            "regression": PLAIN,
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "the same import in the root conftest": (
        {"addition": "import sidefx\n", "base_files": SIDEFX, "regression": PLAIN},
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "a fixture-only conftest in a subdirectory": (
        {"path": "tests/sub/conftest.py", "base_files": SIDEFX, "regression": PLAIN},
        "COMPLETED",
        "",
    ),
    "a new test of a base file leaves process state that flips a base test elsewhere": (
        {
            "addition": LEAK,
            "base_files": SIDEFX,
            "regression": PLAIN,
            "extra_files": {"tests/other/test_a.py": USES_LEAK},
            "verifiers": BOTH,
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "a module-level change in an edited base file leaves that file's base tests out": (
        {
            "base_files": SIDEFX,
            "regression": PLAIN,
            "verifiers": NEW_ONLY,
            "extra_files": {"tests/other/test_env.py": MODULE_LEVEL},
        },
        "COMPLETED",
        "",
    ),
    "a run with the test commit over the time budget": (
        {
            "base_files": SIDEFX,
            "budget": 25,
            "regression": PLAIN,
            "verifiers": BOTH,
            "extra_files": {"tests/other/test_a.py": SLOW_NEW},
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_UNCHECKED",
    ),
    "a conftest in a directory with no base test, compared on the selected files": (
        {"path": "tests/empty/conftest.py", "regression": PLAIN},
        "COMPLETED",
        "",
    ),
    "a conftest import that resolves to a test-commit module beside it": (
        {
            "addition": "import colorsys\n",
            "path": "tests/sub/conftest.py",
            "base_files": LOCAL_MODULE,
            "regression": PLAIN,
            "extra_files": {"tests/sub/colorsys.py": "import os\nos.environ['W3B_FLAG'] = '1'\n"},
            "verifiers": NAMES_MODULE,
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "two conftests: a fixture-only one and a second that flips a base test": (
        {
            "base_files": SHADOWED,
            "regression": PLAIN,
            "extra_files": {"tests/sub/conftest.py": SHADOW},
        },
        "FAILED",
        "TEST_COMMIT_CONFTEST_CHANGES_BASE",
    ),
    "P2P off": ({"mode": "off"}, "FAILED", "TEST_COMMIT_CONFTEST_UNCHECKED"),
}
