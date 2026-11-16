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
    "alias chain": (
        "from helpers import verify_token\n\nhelper = verify_token\ntest_login = helper\n"
    ),
    "chain assigned before use order": (
        "from helpers import verify_token\n\ntest_login = helper\nhelper = verify_token\n"
    ),
    "bound inside an if block": (
        "import sys\nfrom helpers import verify_token\n\n"
        "if sys.platform:\n    test_login = verify_token\n"
    ),
    "imported inside an if block": (
        "import sys\n\nif sys.platform:\n    from helpers import verify_token as test_login\n"
    ),
    "chain inside try": (
        "from helpers import verify_token\n\ntry:\n    h = verify_token\n    test_a = h\n"
        "except Exception:\n    pass\n"
    ),
    "annotated assignment": (
        "from helpers import verify_token\n\ntest_login: object = verify_token\n"
    ),
    "walrus": "from helpers import verify_token\n\n(test_login := verify_token)\n",
    "with target": (
        "from helpers import verify_token\n\nwith verify_token as test_login:\n    pass\n"
    ),
    "for target": (
        "from helpers import verify_token\n\nfor test_login in (verify_token,):\n    pass\n"
    ),
    "attribute binding": "import helpers\n\ntest_login = helpers.verify_token\n",
    "attribute through an import alias": "import helpers as h\n\ntest_login = h.verify_token\n",
    "attribute of a from-imported module": (
        "from tests import helpers\n\ntest_login = helpers.verify_token\n"
    ),
    "a wrapping call": (
        "import pytest\nfrom helpers import verify_token\n\n"
        "test_login = pytest.mark.slow(verify_token)\n"
    ),
    "a class attribute wrapper": (
        "from helpers import verify_token\n\nclass T:\n    pass\n\n"
        "test_login = staticmethod(verify_token)\n"
    ),
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
    "module imported, used only in a test": (
        "import helpers\n\n\ndef test_x():\n    assert helpers.verify_token(1)\n"
    ),
    "attribute bound under a non-test name": ("import helpers\n\nchecker = helpers.verify_token\n"),
    "star import of non-test functions": "from helpers import *\n",
    "non-test name bound in an if block": (
        "import sys\nfrom helpers import verify_token\n\n"
        "if sys.platform:\n    checker = verify_token\n"
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


REEXPORTS = {  # name: base files besides the test module
    "package __init__ re-export": {
        "tests/helpers/__init__.py": "from .auth import verify_token\n",
        "tests/helpers/auth.py": HELPER,
    },
    "module-level rebinding": {
        "tests/helpers.py": "from impl import verify_token as _v\n\nverify_token = _v\n",
        "tests/impl.py": HELPER,
    },
}


@pytest.mark.parametrize("name", list(REEXPORTS))
def test_a_re_exported_helper_is_followed(repo, name):
    base = {MODULE: TAKEN["the Issue example"], **REEXPORTS[name]}
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(dep\)"):
        _check(repo, {CONFTEST: FIXTURE.format("dep")}, path=CONFTEST, base_files=base)


def test_the_unresolved_helper_in_its_own_module_counts_all_parameters(repo):
    helper = "def other(path):\n    return path\n"  # verify_token is not defined here
    base = {MODULE: TAKEN["the Issue example"], "tests/helpers.py": helper}
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(path\)"):
        _check(repo, {CONFTEST: FIXTURE.format("path")}, path=CONFTEST, base_files=base)


@pytest.mark.parametrize("param", ["a, /, b", "*, kw", "pos, /"])
def test_positional_only_and_keyword_only_parameters_are_taken(repo, param):
    name = param.replace("/", "").replace("*", "").replace(",", " ").split()[-1]
    base = {
        MODULE: TAKEN["the Issue example"],
        "tests/helpers.py": f"def verify_token({param}):\n    return 1\n",
    }
    with pytest.raises(dt.TestCommitRejected, match=rf"base test requests \({name}\)"):
        _check(repo, {CONFTEST: FIXTURE.format(name)}, path=CONFTEST, base_files=base)


def test_a_star_import_brings_the_test_named_functions(repo):
    base = {
        MODULE: "from helpers import *\n",
        "tests/helpers.py": "def test_helper(dep):\n    return dep\n",
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(dep\)"):
        _check(repo, {CONFTEST: FIXTURE.format("dep")}, path=CONFTEST, base_files=base)


def test_a_relative_import_resolves_at_its_own_level(repo):
    conftest = "tests/sub/pkg/conftest.py"  # the helper sits outside this directory
    base = {  # one level too high would read tests/helpers.py (parameter "wrong") instead
        "tests/sub/pkg/test_login.py": (
            "from ..helpers import verify_token\n\ntest_x = verify_token\n"
        ),
        "tests/__init__.py": "",
        "tests/sub/__init__.py": "",
        "tests/sub/pkg/__init__.py": "",
        "tests/sub/helpers.py": HELPER,
        "tests/helpers.py": "def verify_token(wrong):\n    return wrong\n",
    }
    with pytest.raises(dt.TestCommitRejected, match=r"base test requests \(dep\)"):
        _check(repo, {conftest: FIXTURE.format("dep")}, path=conftest, base_files=base)
