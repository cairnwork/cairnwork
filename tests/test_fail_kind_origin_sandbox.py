"""#797 end to end in the real sandbox, through ``cairnwork-verify``: the fix is to checkout code
that raises an OSError subclass on the base. That failure is the code under test, so the base
gives teeth: a correct candidate is VERIFIED and a wrong one FAILED (main: UNCOMPARABLE for both,
BASE_ENV_ERROR)."""

import pytest
from conftest import commit_change
from test_uncomparable_paths import run_cli

ADD = "def add(a, b):\n    return a + b + 1\n"
MISSING = "class Missing(FileNotFoundError):\n    pass\n\n"
BASE = ADD + MISSING + "def load(name):\n    raise Missing(2, name)\n"
RIGHT = ADD + MISSING + "def load(name):\n    return {}\n"
WRONG = ADD + MISSING + "def load(name):\n    return None\n"
TEST = "def test_regression():\n    from calc import load\n    assert load('x') == {}\n"


@pytest.mark.sandbox
@pytest.mark.parametrize(("fix", "status", "code"), [(RIGHT, "VERIFIED", 0), (WRONG, "FAILED", 2)])
def test_an_oserror_subclass_from_checkout_code_gives_teeth(project, state, fix, status, code):
    commit_change(project, {"src/calc.py": BASE})
    got, out = run_cli(project, state, TEST, {"src/calc.py": fix})
    assert (got, out["status"], out["verified"]) == (code, status, status == "VERIFIED")
    assert out.get("uncomparable") is None
