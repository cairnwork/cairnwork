"""#1781 item 4 (docs pin): a module-scoped fixture whose setup raises on the base is
BASE_SETUP_ERROR (no teeth); a module-scoped fixture that sets up fine does not hide teeth.
Real Seatbelt, a deferred test commit through the verify CLI."""

import sys

import pytest
from conftest import commit_change
from test_uncomparable_paths import run_cli

KEEP = "def add(a, b):\n    return a + b + 1\n"  # the project's own tests import it
BASE_TEETH = KEEP + "def load():\n    return 0\n"  # a plain assertion failure on the base
FIX = KEEP + "def load():\n    return 1\n"
FIXTURE = (
    "import pytest\n\n@pytest.fixture(scope='module')\ndef conf():\n    {body}\n    return 1\n"
)
TEST = (
    FIXTURE + "\ndef test_regression(conf):\n    from calc import load\n    assert load() == conf\n"
)


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt run")
def test_a_module_fixture_whose_setup_raises_is_base_setup_error(project, state):
    commit_change(project, {"src/calc.py": BASE_TEETH})
    code, out = run_cli(
        project, state, TEST.format(body="raise RuntimeError('no')"), {"src/calc.py": FIX}
    )
    assert out["status"] == "UNCOMPARABLE", out
    assert out["uncomparable"]["code"] == "BASE_SETUP_ERROR", out


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != "darwin", reason="real Seatbelt run")
def test_a_module_fixture_that_sets_up_fine_keeps_the_teeth(project, state):
    commit_change(project, {"src/calc.py": BASE_TEETH})
    code, out = run_cli(project, state, TEST.format(body="pass"), {"src/calc.py": FIX})
    assert out["status"] == "VERIFIED", out
