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
