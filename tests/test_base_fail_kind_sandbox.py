"""#775 Step 1 in the real Seatbelt sandbox, through ``cairnwork-verify``: the shape of the
shadow cases. A fixture whose user-record lookup fails, or a body that raises an OSError, is the
environment deciding the base, so the answer is UNCOMPARABLE and no receipt is made."""

import pytest
from test_uncomparable_paths import FIX_DOUBLE, run_cli

SETUP_LOOKUP = (
    "import pwd\nimport pytest\n"
    "@pytest.fixture\n"
    "def account():\n"
    "    return pwd.getpwnam('cairnwork-no-such-account')\n"
    "def test_double(account):\n"
    "    from calc import double\n"
    "    assert double(2) == 4\n"
)
BODY_OSERROR = (
    "def test_double():\n    from calc import double\n    raise PermissionError(1, 'denied')\n"
)


@pytest.mark.sandbox
def test_a_fixture_whose_user_lookup_fails_is_uncomparable_base_setup_error(project, state):
    code, out = run_cli(project, state, SETUP_LOOKUP, {"src/calc.py": FIX_DOUBLE})
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False
    assert out["uncomparable"]["code"] == "BASE_SETUP_ERROR"
    assert "test_double" in out["uncomparable"]["detail"]
    assert out["core_status"] == "" and out["binding"] is None


@pytest.mark.sandbox
def test_an_oserror_in_the_test_body_is_uncomparable_base_env_error(project, state):
    code, out = run_cli(project, state, BODY_OSERROR, {"src/calc.py": FIX_DOUBLE})
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False
    assert out["uncomparable"]["code"] == "BASE_ENV_ERROR"
    assert out["core_status"] == "" and out["binding"] is None
