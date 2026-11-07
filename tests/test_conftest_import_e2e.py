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
IMPL = (
    "import pytest\n\n\ndef _impl(monkeypatch):\n    def _c(ok):\n"
    "        if monkeypatch is not None:\n            assert ok\n\n    return _c\n\n\n"
)
ALIAS = HELPER.replace("\n\n\n@pytest.fixture", "\n\nf = pytest.fixture\n\n\n@f")
FACTORY = HELPER.replace("pytest\n", "pytest\nfrom deco import make\n", 1).replace(
    "@pytest.fixture", "@make"
)
NESTED = (  # #998 r1 (Claude): built in a factory; the decorator names fixture in a longer string
    "import pytest\n\n\ndef make():\n    @getattr(pytest, 'fixture '.strip())\n"
    "    def guard(monkeypatch):\n        def _c(ok):\n            if monkeypatch is not None:\n"
    "                assert ok\n\n        return _c\n\n    return guard\n\n\nguard = make()\n"
)
TEST = "from calc import add\n\n\ndef test_guard(guard):\n    guard(add(0, 0) < 2)\n"
BASES = {  # name: base files; each registers guard in tests/conftest.py from a helper module
    "from helpers import guard": {"tests/helpers.py": HELPER},
    "pytest_plugins = ['helpers']": {"tests/helpers.py": HELPER},
    "from pkg.deep import guard (re-exported)": {
        "tests/pkg/__init__.py": "", "tests/pkg/deep.py": "from pkg.inner import guard\n",
        "tests/pkg/inner.py": HELPER,
    },
    # #982: the helper registers guard another way
    "from helpers import guard (a two-step fixture call)": {
        "tests/helpers.py": IMPL + "guard = pytest.fixture()(_impl)\n"},
    "from helpers import guard (an alias of pytest.fixture)": {
        "tests/helpers.py": ALIAS},
    "from helpers import guard (a factory decorator from another module)": {
        "tests/helpers.py": FACTORY,
        "tests/deco.py": "import pytest\n\n\ndef make(fn):\n    return pytest.fixture()(fn)\n"},
    "from helpers import guard (an alias another module binds, under another name)": {
        "tests/helpers.py": IMPL.replace("pytest\n", "pytest\nfrom deco import fx as wrap\n", 1)
        + "guard = wrap()(_impl)\n",
        "tests/deco.py": "import pytest\n\nfx = pytest.fixture\n"},
    "from helpers import guard (built in a factory, fixture named in a longer string)": {
        "tests/helpers.py": NESTED},
    "from helpers import guard (an exported alias used as a module attribute)": {
        "tests/helpers.py": IMPL.replace("pytest\n", "pytest\nimport deco\n", 1)
        + "guard = deco.fx()(_impl)\n",
        "tests/deco.py": "import pytest\n\nfx = pytest.fixture\n"},
    "from helpers import guard (an exported alias bound by a tuple assignment)": {
        "tests/helpers.py": IMPL.replace("pytest\n", "pytest\nfrom deco import fx as wrap\n", 1)
        + "guard = wrap()(_impl)\n",
        "tests/deco.py": "import pytest\n\nfx, other = pytest.fixture, 1\n"},
}  # fmt: skip
CALL = "X = pytest.fixture(_impl, name='guard', autouse=True)"
AUTO = "from calc import add\n\n\ndef _impl():\n    assert add(0, 0) < 2\n"
EXTRA = {  # name: base files; tests/sub/conftest.py registers an autouse guard with a name= call
    "from helpers import X (a name= in a call)": {
        "tests/helpers.py": "import pytest\n" + AUTO + CALL + "\n"},
    f"import pytest\nfrom helpers import _impl\n{CALL} (the conftest itself)": {
        "tests/helpers.py": AUTO},
}  # fmt: skip
UNGUARD = "import pytest\n\n\n@pytest.fixture\ndef guard():\n    return lambda ok: None\n"
OVERRIDE = "import pytest\n\n\n@pytest.fixture\ndef monkeypatch():\n    return None\n"
CASES = {  # name: (base, addition to tests/sub/conftest.py, fix, expected status, code)
    **{f"{b}: a nested override of what the helper's fixture requests, wrong fix": (
        b, OVERRIDE, WRONG, "FAILED", "TEST_COMMIT_CONFTEST_STATIC") for b in BASES},
    **{f"{b.split(chr(10))[-1]}: a nested override of that name, wrong fix": (
        b, UNGUARD, WRONG, "FAILED", "TEST_COMMIT_CONFTEST_STATIC") for b in EXTRA},
    "control: a nested new fixture, wrong fix": (
        "from helpers import guard", "import pytest\n" + NEW_FIXTURE, WRONG, "FAILED",
        "p2p-whole-suite: pytest exit code 1"),
    "control: a nested new fixture under a name= in a call, correct fix": (
        list(EXTRA)[1], "import pytest\n" + NEW_FIXTURE, FIX, "COMPLETED", ""),
    "control: a nested new fixture under a name= in a call, wrong fix": (
        list(EXTRA)[1], "import pytest\n" + NEW_FIXTURE, WRONG, "FAILED", "exit code 1"),
    "control: a nested new fixture, correct fix": (
        "from helpers import guard", "import pytest\n" + NEW_FIXTURE, FIX, "COMPLETED", ""),
}  # fmt: skip


@pytest.fixture
def project(_project):  # noqa: F811
    return _project


@pytest.mark.parametrize("name", CASES)
def test_an_override_of_an_imported_fixtures_dependency_is_refused(project, tmp_path, name):
    base, addition, fix, status, code = CASES[name]
    at, deeper = ("tests/sub/", "deeper/") if base in EXTRA else ("tests/", "")  # #982 rows
    files = {"tests/conftest.py": BASE_CONFTEST, **{**BASES, **EXTRA}[base]}
    files[f"{at}conftest.py"] = BASE_CONFTEST + base.split(" (")[0] + "\n"
    files[f"tests/sub/{deeper}test_guard.py"] = TEST if not deeper else "def test_a():\n    pass\n"
    result = conftest_run(
        project, tmp_path, addition=addition, regression=PLAIN, fix=fix, base_files=files,
        path=f"tests/sub/{deeper}conftest.py",
    )  # fmt: skip
    if table := os.environ.get("SCENARIO_TABLE"):  # the before/after table of the PR body
        row = {"name": name, "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == status, result.reason
    assert code in result.reason


UNRELATED = {  # #998 r2 (Claude B4): an unrelated import shares a name a test file binds
    "tests/conftest.py": BASE_CONFTEST + "from helpers import sh  # noqa: F401\n",
    "tests/helpers.py": "from subprocess import run\n\n\n"
    "def sh(*a, **kw):\n    return run(*a, **kw)\n",
    "tests/sub/test_data.py": "def load_fixture(n):\n    return n\n\n\n"
    "run = load_fixture('x')\n\n\ndef test_data():\n    assert run == 'x'\n",
}  # fmt: skip


def test_a_benign_nested_fixture_beside_an_unrelated_name_is_accepted(project, tmp_path):
    result = conftest_run(
        project, tmp_path, addition="import pytest\n" + NEW_FIXTURE, regression=PLAIN, fix=FIX,
        base_files=UNRELATED, path="tests/sub/conftest.py",
    )  # fmt: skip
    if table := os.environ.get("SCENARIO_TABLE"):
        row = {"name": "B4 benign", "status": result.status, "reason": result.reason[:160]}
        Path(table).open("a").write(json.dumps(row) + "\n")
    assert result.status == "COMPLETED", result.reason
