"""#987: a helper a test module imports and binds under a test name is collected by pytest with its
own parameters, so those parameters are taken (conftest_names._test_bound_helpers). Other imports
of a test module stay unread (#982)."""

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

CONFTEST = "tests/sub/conftest.py"
FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
HELPER = "def verify_token(dep):\n    return dep\n"
MODULE = "tests/sub/test_login.py"
TAKEN = {  # name: test module text
    "the Issue example": "from helpers import verify_token\n\ntest_login = verify_token\n",
    "bound through an alias": "from helpers import verify_token as v\n\ntest_login = v\n",
    "imported under a test name": "from helpers import verify_token as test_login\n",
    "bound next to other names": (
        "from helpers import verify_token\n\nX = 1\ntest_a, test_b = verify_token, 2\n"
    ),
}
FREE = {  # name: test module text
    "imported, not bound under a test name": (
        "from helpers import verify_token\n\n\ndef test_login():\n    assert verify_token(1)\n"
    ),
    "bound under a non-test name": "from helpers import verify_token\n\nchecker = verify_token\n",
    "imported, called in a test": (
        "from helpers import verify_token\n\n\ndef test_x():\n    test_y = verify_token(1)\n"
    ),
    "a test name bound to another name": (
        "from helpers import verify_token\n\nother = 1\ntest_login = other\n"
    ),
}


@pytest.mark.parametrize("name", list(TAKEN))
def test_a_helper_bound_under_a_test_name_is_taken(repo, name):
    base = {MODULE: TAKEN[name], "tests/helpers.py": HELPER}
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(dep\)"):
        _check(repo, {CONFTEST: FIXTURE.format("dep")}, path=CONFTEST, base_files=base)


@pytest.mark.parametrize("name", list(FREE))
def test_a_helper_not_bound_under_a_test_name_is_free(repo, name):
    base = {MODULE: FREE[name], "tests/helpers.py": HELPER}
    _check(repo, {CONFTEST: FIXTURE.format("dep")}, path=CONFTEST, base_files=base)


def test_only_the_bound_helper_parameters_are_taken(repo):
    helper = HELPER + "\n\ndef other(path):\n    return path\n"
    base = {MODULE: TAKEN["the Issue example"], "tests/helpers.py": helper}
    _check(repo, {CONFTEST: FIXTURE.format("path")}, path=CONFTEST, base_files=base)


def test_a_relative_import_is_followed(repo):
    base = {
        MODULE: "from ..helpers import verify_token\n\ntest_login = verify_token\n",
        "tests/__init__.py": "",
        "tests/sub/__init__.py": "",
        "tests/helpers.py": HELPER,
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(dep\)"):
        _check(repo, {CONFTEST: FIXTURE.format("dep")}, path=CONFTEST, base_files=base)
