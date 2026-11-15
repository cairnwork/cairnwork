"""#1004 residual: refuse imported fixture registrations hidden by indirect callable forms."""

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

ABOVE = "tests/conftest.py"
HELPER = "tests/helpers.py"
NESTED = "tests/sub/conftest.py"
PLUGIN = "pytest_plugins = ['helpers']\n"
GUARD = "import pytest\n\n\n@pytest.fixture\ndef guard(dep):\n    return dep\n"


MUST_BLOCK = {
    "function imported from another module": {
        "tests/other.py": "def _g(dep):\n    return dep\n",
        HELPER: "import pytest\nfrom other import _g\nguard = pytest.fixture()(_g)\n",
    },
    "lambda registration": {
        HELPER: "import pytest\nguard = pytest.fixture()(lambda dep: dep)\n",
    },
    "wrapper call around function": {
        HELPER: "import pytest\ndef wrap(f):\n    return f\n"
        "def _g(dep):\n    return dep\nguard = pytest.fixture()(wrap(_g))\n",
    },
    "class attribute registration": {
        HELPER: "import pytest\nclass K:\n    def g(dep):\n        return dep\n"
        "guard = pytest.fixture()(K.g)\n",
    },
    "walrus function value": {
        HELPER: "import pytest\ndef _i(dep):\n    return dep\n"
        "guard = pytest.fixture()((_g := _i))\n",
    },
    "for target callable alias": {
        HELPER: "import pytest\nfor fx in [pytest.fixture]:\n    pass\n"
        "def _g(dep):\n    return dep\nguard = fx(_g)\n",
    },
    "with target callable alias": {
        HELPER: "import contextlib, pytest\n"
        "with contextlib.nullcontext(pytest.fixture) as fx:\n    pass\n"
        "def _g(dep):\n    return dep\nguard = fx(_g)\n",
    },
    "getattr alias": {
        "tests/fxm.py": "import pytest\nfx = pytest.fixture\n",
        HELPER: "import fxm\nfx = getattr(fxm, 'fx')\ndef _g(dep):\n    return dep\n"
        "guard = fx(_g)\n",
    },
    "alias created by call in imported module": {
        "tests/fxm.py": "import pytest\nfx = pytest.fixture(scope='function')\n",
        HELPER: "from fxm import fx\ndef _g(dep):\n    return dep\nguard = fx(_g)\n",
    },
    "partial alias created in imported module": {
        "tests/fxm.py": "import functools, pytest\n"
        "fx = functools.partial(pytest.fixture, scope='function')\n",
        HELPER: "from fxm import fx\ndef _g(dep):\n    return dep\nguard = fx()(_g)\n",
    },
}


MUST_PASS = {
    "unrelated fixture parameter": {
        HELPER: "import pytest\n\n\n@pytest.fixture\ndef guard(path):\n    return path\n",
    },
    "unrelated plain helper parameter": {
        HELPER: "def helper(path):\n    return path\n",
    },
    "fixture decorator remains accepted": {
        HELPER: "import pytest\n\n\n@pytest.fixture\ndef guard():\n    return 1\n",
    },
}


@pytest.mark.parametrize("case", list(MUST_BLOCK))
def test_indirect_imported_fixture_registrations_are_refused(repo, case):
    files = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        HELPER: MUST_BLOCK[case].get(HELPER, ""),
        **{path: text for path, text in MUST_BLOCK[case].items() if path != HELPER},
        NESTED: "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n",
    }
    base = {ABOVE: files[ABOVE], **{path: text for path, text in MUST_BLOCK[case].items()}}
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(repo, {NESTED: files[NESTED]}, path=NESTED, base_files=base)
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "a name the base binds or a base test requests (dep)" in str(refused.value)


@pytest.mark.parametrize("case", list(MUST_PASS))
def test_unrelated_imported_changes_remain_accepted(repo, case):
    files = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        HELPER: MUST_PASS[case][HELPER],
    }
    _check(
        repo,
        {NESTED: "import pytest\n\n\n@pytest.fixture\ndef fresh():\n    return 1\n"},
        path=NESTED,
        base_files=files,
    )


@pytest.mark.parametrize("case", list(MUST_BLOCK))
def test_indirect_imported_fixture_controls_do_not_conflict(repo, case):
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        **{path: text.replace("dep", "path") for path, text in MUST_BLOCK[case].items()},
    }
    _check(
        repo,
        {NESTED: "import pytest\n\n\n@pytest.fixture\ndef fresh():\n    return 1\n"},
        path=NESTED,
        base_files=base,
    )


# Review record r1 (#1286): benign helper code must not be refused, and forms that only the
# call-argument walk catches must stay refused.
OTHER_G = {"tests/other.py": "def _g(dep):\n    return dep\n"}
WRAP = "def wrap(fn):\n    return fn\n"
G_DEF = "def _g(dep):\n    return dep\n"

BENIGN = {
    "lambda parameter x": ("x", "ITEMS = sorted([3, 1], key=lambda x: -x)\n"),
    "lambda parameter row": ("row", "ROWS = sorted([(1, 2)], key=lambda row: row[0])\n"),
    "nested keyword timeout": ("timeout", "CFG = dict(a=dict(timeout=3))\n"),
    "generator variable item": ("item", "L = list(item for item in range(3))\n"),
    "list comprehension variable": ("elem", "L = list([elem for elem in range(3)])\n"),
    "keyword scope of unrelated call": (
        "scope",
        "import pytest\nR = list(pytest.fixture(scope='function')(len))\n",
    ),
    "attribute name": ("name", "import os\nN = str(os.path.basename(__file__).name)\n"),
    "flag constant I": ("I", "import re\nRX = re.compile('a', re.I)\n"),
    "keyword value session": ("session", "client = make(session=session)\n"),
    "keyword name only": ("retries", "CALL = make(retries=3)\n"),
    "dict comprehension key": ("k", "D = dict({k: 1 for k in 'ab'})\n"),
    "lambda default": ("d", "F = sorted([1], key=lambda v, d=1: v)\n"),
    "annotated assignment lambda": ("q", "A: list = sorted([1], key=lambda q: q)\n"),
    "augmented assignment keyword": ("w", "A = []\nA += sorted([1], key=lambda w: w)\n"),
}

BLOCKED_FORMS = {
    "lambda with unused parameter": {
        HELPER: "import pytest\nguard = pytest.fixture()(lambda dep: 1)\n",
    },
    "lambda wrapped by call": {
        HELPER: "import pytest\n" + WRAP + "guard = pytest.fixture()(wrap(lambda dep: 1))\n",
    },
    "keyword argument function": {
        HELPER: "import pytest\n" + WRAP + G_DEF + "guard = pytest.fixture()(wrap(fn=_g))\n",
    },
    "annotated assignment imported function": {
        **OTHER_G,
        HELPER: "import pytest\nfrom other import _g\nguard: object = pytest.fixture()(_g)\n",
    },
    "augmented assignment imported function": {
        **OTHER_G,
        HELPER: "import pytest\nfrom other import _g\nguard = []\nguard += pytest.fixture()(_g)\n",
    },
    "annotated assignment local function": {
        HELPER: "import pytest\n" + G_DEF + "guard: object = pytest.fixture()(_g)\n",
    },
    "augmented assignment local function": {
        HELPER: "import pytest\n" + G_DEF + "guard = []\nguard += pytest.fixture()(_g)\n",
    },
    "keyword function through imported alias": {
        **OTHER_G,
        HELPER: "import pytest\nfrom other import _g as h\n"
        + WRAP
        + "guard = pytest.fixture()(wrap(fn=h))\n",
    },
}


@pytest.mark.parametrize("case", list(BENIGN))
def test_benign_helper_call_words_are_not_taken_names(repo, case):
    name, helper = BENIGN[case]
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        HELPER: helper,
    }
    nested = f"import pytest\n\n\n@pytest.fixture\ndef {name}():\n    return 1\n"
    _check(repo, {NESTED: nested}, path=NESTED, base_files=base)


@pytest.mark.parametrize("case", list(BLOCKED_FORMS))
def test_registration_forms_found_only_through_call_arguments_are_refused(repo, case):
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        **BLOCKED_FORMS[case],
    }
    nested = "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n"
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(repo, {NESTED: nested}, path=NESTED, base_files=base)
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "a name the base binds or a base test requests (dep)" in str(refused.value)


# Review r2 (Codex on 3b99aa56): the same function reached through a module alias or attribute.
PKG_G = {"tests/pkg/other.py": "def _g(dep):\n    return dep\n", "tests/pkg/__init__.py": ""}
MODULE_FORMS = {
    "import other as o": (
        OTHER_G,
        "import pytest\nimport other as o\nguard = pytest.fixture()(o._g)\n",
    ),
    "import other directly": (
        OTHER_G,
        "import pytest\nimport other\nguard = pytest.fixture()(other._g)\n",
    ),
    "import a.b as o": (
        PKG_G,
        "import pytest\nimport pkg.other as o\nguard = pytest.fixture()(o._g)\n",
    ),
    "import a.b dotted use": (
        PKG_G,
        "import pytest\nimport pkg.other\nguard = pytest.fixture()(pkg.other._g)\n",
    ),
    "from a import b": (
        PKG_G,
        "import pytest\nfrom pkg import other\nguard = pytest.fixture()(other._g)\n",
    ),
    "from . import other": (
        OTHER_G,
        "import pytest\nfrom . import other\nguard = pytest.fixture()(other._g)\n",
    ),
    "alias in wrapper call": (
        OTHER_G,
        "import pytest\nimport other as o\n" + WRAP + "guard = pytest.fixture()(wrap(fn=o._g))\n",
    ),
}


@pytest.mark.parametrize("case", list(MODULE_FORMS))
def test_module_alias_registrations_are_refused(repo, case):
    others, helper = MODULE_FORMS[case]
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        **others,
        HELPER: helper,
    }
    nested = "import pytest\n\n\n@pytest.fixture\ndef dep():\n    return 1\n"
    with pytest.raises(dt.TestCommitRejected) as refused:
        _check(repo, {NESTED: nested}, path=NESTED, base_files=base)
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"


@pytest.mark.parametrize("case", list(MODULE_FORMS))
def test_module_alias_controls_do_not_conflict(repo, case):
    others, helper = MODULE_FORMS[case]
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        **{path: text.replace("dep", "path") for path, text in others.items()},
        HELPER: helper,
    }
    nested = "import pytest\n\n\n@pytest.fixture\ndef fresh():\n    return 1\n"
    _check(repo, {NESTED: nested}, path=NESTED, base_files=base)


# Deliberate fail-closed trade-off (review r3): a helper function passed as a call argument may be
# registered as a fixture by the callee, which static reading cannot tell. A nested fixture that
# shares the helper's name is therefore refused; the same name with no call passing it is accepted.
SAME_NAME_PASSED = "def session():\n    return 1\n\n\nclient = make(session=session)\n"
SAME_NAME_NOT_PASSED = "def session():\n    return 1\n\n\nclient = make()\n"


def _same_name_check(repo, helper):
    base = {
        ABOVE: "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n" + PLUGIN,
        HELPER: helper,
    }
    nested = "import pytest\n\n\n@pytest.fixture\ndef session():\n    return 1\n"
    _check(repo, {NESTED: nested}, path=NESTED, base_files=base)


def test_same_name_helper_passed_to_a_call_is_refused_conservatively(repo):
    with pytest.raises(dt.TestCommitRejected) as refused:
        _same_name_check(repo, SAME_NAME_PASSED)
    assert refused.value.code == "TEST_COMMIT_CONFTEST_STATIC"
    assert "session" in str(refused.value)


def test_same_name_helper_not_passed_to_a_call_is_accepted(repo):
    _same_name_check(repo, SAME_NAME_NOT_PASSED)
