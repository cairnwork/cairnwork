"""#982: a helper's parameter is no fixture request, so a new fixture may take its name; every
form pytest does resolve a fixture for is still counted (conftest_names.requested)."""

import pytest
from test_conftest_names import BASE, _check

import cairnwork.deferred_tests as dt

FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
CONFTEST = "tests/sub/conftest.py"
H = "tests/sub/helpers.py"
STAR = "from helpers import *  # noqa: F403\n"
A = "def check_a(dep):\n    return dep\n\n\n"  # a helper nothing names
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
    "a helper named testit a test module star-imports (pytest's default prefix is test)": (
        "env", {H: "def testit(env):\n    pass\n", "tests/sub/test_s.py": STAR},
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
    "a helper found by a literal getattr string": (
        "dep", {H: "import sys\n\n\n" + A + "test_f = getattr(sys.modules[__name__], 'check_a')\n",
                "tests/sub/test_g.py": "from helpers import test_f  # noqa: F401\n"},
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
    "a helper inside a test module (every function of a test module counts, r3)": (
        "data",
        {"tests/sub/test_h.py": "def _build(data):\n    return data\n\n\n"
         "def test_h():\n    assert _build(1)\n"},
    ),
    # r3 of #984 (Gemini r2, jeter r2): a construct that may name or collect a function where it
    # cannot be read makes every function count; each case is caught by that construct alone
    "a helper module renaming through globals()": (
        "dep", {H: A + "for _n, _f in list(globals().items()):\n    pass\n"}),
    "setattr with a computed name": (
        "dep", {H: A + "class Box:\n    pass\n\n\nfor _n in dir():\n"
                "    setattr(Box, 'test_' + _n, None)\n"}),
    "a metaclass": (
        "dep", {H: "class Meta(type):\n    pass\n\n\nclass Box(metaclass=Meta):\n"
                "    def check_a(self, dep):\n        return dep\n"}),
    "__init_subclass__": (
        "dep", {H: "class Base:\n    def __init_subclass__(cls):\n        pass\n\n\n"
                "class Box(Base):\n    def check_a(self, dep):\n        return dep\n"}),
    "__dict__": ("dep", {H: A + "NAMES = list(object.__dict__)\n"}),
    "type() with three arguments": ("dep", {H: A + "Box = type('Box', (), {})\n"}),
    "import_module": (
        "dep", {H: "import importlib\n\nM = importlib.import_module('x')\n\n\n" + A}),
    "getattr with a computed name": (
        "dep", {H: A + "def find(m, n):\n    return getattr(m, 'check_' + n)\n"}),
    "pytest_plugins in a base conftest above": (
        "dep", {"tests/conftest.py": BASE + "pytest_plugins = ['plug']\n", H: A}),
    "a plugin registered by a base conftest above": (
        "dep", {"tests/conftest.py": BASE + "\n\ndef pytest_configure(config):\n"
                "    config.pluginmanager.register(object())\n", H: A}),
    "the ini usefixtures option": ("iu", {"pytest.ini": "[pytest]\nusefixtures = iu other\n"}),
    "a base python_functions setting: every function counts again": (
        "cfg", {"pytest.ini": "[pytest]\npython_functions = check_*\n",
                H: "def check_it(cfg):\n    pass\n", "tests/sub/test_s.py": STAR},
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
