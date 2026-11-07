"""#982: a helper's parameter is no fixture request, so a new fixture may take its name; every
form pytest does resolve a fixture for is still counted (conftest_names.requested)."""

import pytest
from test_conftest_names import BASE, _check

import cairnwork.deferred_tests as dt

FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
CONFTEST = "tests/sub/conftest.py"
HELPERS = (
    "def make(path, data=None):\n    return path\n\n\n"
    "class Box:\n    def put(self, item):\n        return item\n"
)

FREE = {  # name: (new fixture, base files)
    "a helper function's parameter": ("path", {"tests/sub/helpers.py": HELPERS}),
    "a helper method's parameter": ("item", {"tests/sub/helpers.py": HELPERS}),
    "a helper another file imports and calls": (
        "path",
        {"tests/sub/helpers.py": "def make(path):\n    return path\n",
         "tests/sub/test_h.py": "from helpers import make\n\n\ndef test_h():\n"
         "    assert make(1)\n"},
    ),
    "a helper beside a test that calls it": (
        "data",
        {"tests/sub/test_h.py": "def _build(data):\n    return data\n\n\n"
         "def test_h():\n    assert _build(1)\n"},
    ),
}  # fmt: skip

STILL_TAKEN = {  # name: (new fixture, base files)
    "a test function's parameter": ("env", {"tests/sub/test_e.py": "def test_e(env):\n    pass\n"}),
    "a test method's parameter": (
        "env", {"tests/sub/test_e.py": "class TestE:\n    def test_e(self, env):\n        pass\n"},
    ),
    "a fixture's parameter": (
        "dep", {"tests/sub/helpers.py": "import pytest\n\n\n@pytest.fixture\ndef f(dep):\n"
                "    return dep\n"},
    ),
    "a function wrapped by pytest.fixture as a value": (
        "dep", {"tests/sub/helpers.py": "import pytest\n\n\ndef plain(dep):\n    return dep\n\n\n"
                "wrapped = pytest.fixture(plain)\n"},
    ),
    "a method wrapped by pytest.fixture as a value": (
        "thing", {"tests/sub/helpers.py": "import pytest\n\n\nclass Box:\n"
                  "    def put(self, thing):\n        return thing\n\n\n"
                  "wrapped = pytest.fixture(Box().put)\n"},
    ),
    "a pytest hook's parameter": (
        "metafunc", {"tests/sub/helpers.py": "def pytest_generate_tests(metafunc):\n    pass\n"},
    ),
    "usefixtures in pytestmark": (
        "pm", {"tests/sub/test_m.py": "import pytest\n\n"
               "pytestmark = pytest.mark.usefixtures('pm')\n\n\ndef test_m():\n    pass\n"},
    ),
    "a literal getfixturevalue in a helper": (
        "gv", {"tests/sub/helpers.py": "def get(request):\n"
               "    return request.getfixturevalue('gv')\n"},
    ),
    "a getfixturevalue it cannot read: every function counts again": (
        "data", {"tests/sub/helpers.py": "def get(request, name):\n"
                 "    return request.getfixturevalue(name)\n\n\n"
                 "def make(data):\n    return data\n"},
    ),
    "a test named testit (pytest's default prefix is test)": (
        "env", {"tests/sub/test_t.py": "def testit(env):\n    pass\n"},
    ),
    # r2 of #984 (Gemini r1, jeter r1): pytest collects across files
    "a helper another file imports under a test name": (
        "dep", {"tests/sub/checks.py": "def guard(dep):\n    return dep\n",
                "tests/sub/test_g.py": "from checks import guard as test_guard\n"},
    ),
    "a helper another file assigns from a module attribute": (
        "dep", {"tests/sub/checks.py": "def guard(dep):\n    return dep\n",
                "tests/sub/test_g.py": "import checks\n\ntest_guard = checks.guard\n"},
    ),
    "a helper another file assigns by name (the helper's file read last)": (
        "dep", {"tests/sub/vchecks.py": "def guard(dep):\n    return dep\n",
                "tests/sub/test_g.py": "from vchecks import guard\n\ntest_guard = guard\n"},
    ),
    "a helper found by a string": (
        "dep", {"tests/sub/test_g.py": "def guard(dep):\n    return dep\n\n\n"
                "test_guard = globals()['guard']\n"},
    ),
    "a base collection hook above: every function counts again": (
        "dep", {"tests/conftest.py": BASE + "\n\ndef pytest_pycollect_makeitem(collector, name, "
                "obj):\n    return None\n", "tests/sub/checks.py": "def check_it(dep):\n"
                "    return dep\n"},
    ),
    "a request another file cannot read: every function counts again": (
        "data", {"tests/sub/getter.py": "def get(request, name):\n"
                 "    return request.getfixturevalue(name)\n",
                 "tests/sub/helpers.py": "def make(data):\n    return data\n"},
    ),
    "the ini usefixtures option": ("iu", {"pytest.ini": "[pytest]\nusefixtures = iu other\n"}),
    "a base python_functions setting: every function counts again": (
        "cfg", {"pytest.ini": "[pytest]\npython_functions = check_*\n",
                "tests/sub/test_c.py": "def check_it(cfg):\n    pass\n"},
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", list(FREE))
def test_a_helper_parameter_name_is_free(repo, name):
    fixture, base_files = FREE[name]
    _check(repo, {CONFTEST: FIXTURE.format(fixture)}, path=CONFTEST, base_files=base_files)


@pytest.mark.parametrize("name", list(STILL_TAKEN))
def test_a_name_pytest_resolves_as_a_fixture_is_still_taken(repo, name):
    fixture, base_files = STILL_TAKEN[name]
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({fixture}\\)"):
        _check(repo, {CONFTEST: FIXTURE.format(fixture)}, path=CONFTEST, base_files=base_files)


def test_an_unreadable_base_configuration_refuses(repo):
    broken = {"pytest.ini": "[pytest\nusefixtures = x\n"}
    with pytest.raises(dt.TestCommitRejected, match="cannot read the base pytest configuration"):
        _check(repo, {CONFTEST: FIXTURE.format("free")}, path=CONFTEST, base_files=broken)
