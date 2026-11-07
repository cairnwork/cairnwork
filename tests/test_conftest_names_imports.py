"""#982 item 4: a fixture is registered where it is imported but requests its dependencies in its
own module, so what the base modules a counted file imports request counts too
(conftest_names._imported)."""

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt

CONFTEST = "tests/sub/conftest.py"
FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
PATH = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
AUTO = "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef strict():\n    return True\n"
GUARD = "import pytest\n\n\n@pytest.fixture\ndef guard(dep):\n    return dep\n"
ABOVE = "tests/conftest.py"
TAKEN = {  # name: (new fixture, base files)
    "from helpers import guard": ("dep", {ABOVE: "from helpers import guard\n",
                                          "tests/helpers.py": GUARD}),
    "a relative import": ("dep", {ABOVE: "from .helpers import guard\n", "tests/__init__.py": "",
                                  "tests/helpers.py": GUARD}),
    "pytest_plugins as a comma string": (
        "dep", {ABOVE: "pytest_plugins = 'other, helpers'\n", "tests/helpers.py": GUARD}),
    "pytest_plugins extended with +=": (
        "dep", {ABOVE: "pytest_plugins = []\npytest_plugins += ['helpers']\n",
                "tests/helpers.py": GUARD}),
    "pytest_plugins it cannot read: every base file": (
        "dep", {ABOVE: "NAMES = ['far.fix']\npytest_plugins = [n for n in NAMES]\n",
                "tests/far/fix.py": GUARD}),
    "a re-exported fixture": (
        "dep", {ABOVE: "from pkg.deep import guard\n", "tests/pkg/__init__.py": "",
                "tests/pkg/deep.py": "from pkg.inner import guard\n", "tests/pkg/inner.py": GUARD}),
    "import M, then a fixture assigned from it": (
        "dep", {ABOVE: "import helpers\n\nguard = helpers.guard\n", "tests/helpers.py": GUARD}),
    "from a package import a submodule": (
        "dep", {ABOVE: "from pkg import inner\n\nguard = inner.guard\n",
                "tests/pkg/__init__.py": "", "tests/pkg/inner.py": GUARD}),
    "a fixture's name= in the imported module": (
        "alias", {ABOVE: "from helpers import _g\n", "tests/helpers.py":
                  "import pytest\n\n\n@pytest.fixture(name='alias')\ndef _g():\n    return 1\n"}),
    # r2 (jeter r1, Gemini r1): the name an imported fixture is registered under is taken too
    "a fixture a star import registers": ("guard", {ABOVE: "from helpers import *  # noqa: F403\n",
                                                    "tests/helpers.py": GUARD}),
    "a fixture imported under another name": (
        "guard", {ABOVE: "from helpers import guard as g  # noqa: F401\n",
                  "tests/helpers.py": GUARD}),
    "an autouse fixture pytest_plugins registers": (
        "strict", {ABOVE: "pytest_plugins = ['helpers']\n", "tests/helpers.py": AUTO}),
    "pytest_plugins.append": (
        "dep", {ABOVE: "pytest_plugins = []\npytest_plugins.append('helpers')\n",
                "tests/helpers.py": GUARD}),
    "another call on pytest_plugins: every base file": (
        "dep", {ABOVE: "pytest_plugins = []\npytest_plugins.insert(0, 'far.fix')\n",
                "tests/far/fix.py": GUARD}),
    "a conftest below the directory imports a fixture": (
        "dep", {"tests/sub/deeper/conftest.py": "from shared2 import guard  # noqa: F401\n",
                "tests/shared2.py": GUARD}),
    "a fixture wrapped by pytest.fixture in the imported module": (
        "dep", {ABOVE: "from helpers import guard\n", "tests/helpers.py": "import pytest\n\n\n"
                "def plain(dep):\n    return dep\n\n\nguard = pytest.fixture(plain)\n"}),
    "a usefixtures string in the imported module": (
        "dep2", {ABOVE: "from helpers import guard\n", "tests/helpers.py": "import pytest\n\n\n"
                 "@pytest.fixture\n@pytest.mark.usefixtures('dep2')\n"
                 "def guard():\n    return 1\n"}),
}  # fmt: skip
FREE = {  # name: (new fixture, base files)
    "pytest_plugins.append reads only the module it names": (
        "dep", {ABOVE: "pytest_plugins = []\npytest_plugins.append('helpers')\n",
                "tests/helpers.py": "X = 1\n", "tests/far/fix.py": GUARD}),
    "a plain helper function a conftest imports (r2: only fixtures count)": (
        "path", {ABOVE: "from helpers import make\n",
                 "tests/helpers.py": "def make(path):\n    return path\n"}),
    "a helper module a test module imports (test-module imports: #987)": (
        "path", {"tests/sub/test_h.py": "from shared import make\n\n\ndef test_h():\n"
                 "    assert make(1)\n", "tests/shared.py": "def make(path):\n    return path\n"}),
    "a module in no base file (third-party, not seen)": (
        "dep", {ABOVE: "from someplugin import guard\n"}),
}  # fmt: skip


@pytest.mark.parametrize("name", list(TAKEN))
def test_what_an_imported_fixture_requests_is_taken(repo, name):
    fixture, base_files = TAKEN[name]
    files = {**base_files, ABOVE: PATH + base_files.get(ABOVE, "")}
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({fixture}\\)"):
        _check(repo, {CONFTEST: FIXTURE.format(fixture)}, path=CONFTEST, base_files=files)


@pytest.mark.parametrize("name", list(FREE))
def test_a_name_no_imported_fixture_requests_is_free(repo, name):
    fixture, base_files = FREE[name]
    files = {**base_files, ABOVE: PATH + base_files.get(ABOVE, "")}
    _check(repo, {CONFTEST: FIXTURE.format(fixture)}, path=CONFTEST, base_files=files)


def test_more_imported_files_than_the_cap_refuse(repo, monkeypatch):
    import cairnwork.conftest_names as names

    monkeypatch.setattr(names, "MAX_FILES", 1)
    base = {ABOVE: PATH + "from a import x\nfrom b import y\nfrom c import z\n",
            "tests/a.py": "x = 1\n", "tests/b.py": "y = 1\n", "tests/c.py": "z = 1\n"}  # fmt: skip
    with pytest.raises(dt.TestCommitRejected, match="imported base Python files"):
        _check(repo, {CONFTEST: FIXTURE.format("free")}, path=CONFTEST, base_files=base)


def test_what_a_test_module_imports_is_not_read(repo, monkeypatch):
    """Only conftests' imports are read; a test module's (#987) would put app code under the cap."""
    import cairnwork.conftest_names as names

    monkeypatch.setattr(names, "MAX_FILES", 1)
    base = {"tests/sub/test_h.py": "from a import x\nfrom b import y\nfrom c import z\n\n\n"
            "def test_h():\n    assert x\n", "tests/a.py": "x = 1\n", "tests/b.py": "y = 1\n",
            "tests/c.py": "z = 1\n"}  # fmt: skip
    _check(repo, {CONFTEST: FIXTURE.format("free")}, path=CONFTEST, base_files=base)
