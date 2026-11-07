"""#982 / #988 follow-up: ways of registering a fixture that conftest_names reads since #982.

Each case is a base file pair whose fixture pytest 9 registers and a nested override of a name it
depends on (or of its registered name) turns off; conftest_names takes that name. Plain pytest
confirms every form registers the fixture and that the nested override disables it. They were
strict xfails on main (pre-existing before #988 too)."""

import ast

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt
from cairnwork.conftest_names import _fixtures, bound
from cairnwork.fixture_forms import exported
from cairnwork.fixture_forms import registered as _registered

CONFTEST = "tests/sub/conftest.py"
FIXTURE = "import pytest\n\n\n@pytest.fixture\ndef {}():\n    return 1\n"
PATH = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
ABOVE = "tests/conftest.py"
CHECK = "if dep != 'weak':\n        pytest.fail('guard')\n"
MISSED = {  # name: (taken name, base files)
    "an alias decorator assigned from pytest.fixture": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n", "tests/helpers.py":
                "import pytest\n\nf = pytest.fixture\n\n\n@f(autouse=True)\ndef guard(dep):\n"
                "    " + CHECK}),
    "an alias decorator imported as another name": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n", "tests/helpers.py":
                "import pytest\nfrom pytest import fixture as fx\n\n\n@fx(autouse=True)\n"
                "def guard(dep):\n    " + CHECK}),
    "a two-step pytest.fixture(...)(fn)": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n", "tests/helpers.py":
                "import pytest\n\n\ndef _impl(dep):\n    " + CHECK + "\n\n"
                "guard = pytest.fixture(autouse=True)(_impl)\n"}),
    "the name an assignment registers": (
        "guard", {ABOVE: "from helpers import *  # noqa: F403\n", "tests/helpers.py":
                  "import pytest\n\n\ndef _impl():\n    pytest.fail('guard')\n\n\n"
                  "guard = pytest.fixture(_impl, autouse=True)\n"}),
    "a name= from a variable": (
        "alias", {ABOVE: "from helpers import _g  # noqa: F401\n", "tests/helpers.py":
                  "import pytest\n\nCUSTOM = 'alias'\n\n\n"
                  "@pytest.fixture(name=CUSTOM, autouse=True)\n"
                  "def _g():\n    pytest.fail('guard')\n"}),
}  # fmt: skip


@pytest.mark.parametrize("name", list(MISSED))
def test_a_fixture_registered_another_way_is_taken(repo, name):
    taken, base_files = MISSED[name]
    files = {**base_files, ABOVE: PATH + base_files.get(ABOVE, "")}
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({taken}\\)"):
        _check(repo, {CONFTEST: FIXTURE.format(taken)}, path=CONFTEST, base_files=files)


# Same root cause, found by the post-merge variant hunt of #988 (also pre-existing on main): a
# fixture's registered name is read only from a string ``name=`` on a decorator of a top-level def.
IMPL = "import pytest\n\n\ndef _impl():\n    pytest.fail('guard')\n\n\n"
IF_GUARD = ("import sys\nimport pytest\n\nif sys.platform:\n    @pytest.fixture(autouse=True)\n"
            "    def guard():\n        pytest.fail('guard')\n")  # fmt: skip
NAME_VAR = ("import pytest\n\nCUSTOM = 'alias'\n\n\n@pytest.fixture(name=CUSTOM, autouse=True)\n"
            "def _g():\n    pytest.fail('guard')\n")  # fmt: skip
CALL_NAME = IMPL + "X = pytest.fixture(_impl, name='alias', autouse=True)\n"
TWO_STEP_NAME = IMPL + "X = pytest.fixture(name='alias', autouse=True)(_impl)\n"
MISSED_IMPORTED = {  # name: (taken name, base files)
    "a name= in a call that registers it": (
        "alias", {ABOVE: "from helpers import X  # noqa: F401\n", "tests/helpers.py": CALL_NAME}),
    "a name= in a two-step call": (
        "alias", {ABOVE: "from helpers import X  # noqa: F401\n",
                  "tests/helpers.py": TWO_STEP_NAME}),
    "an alias another module binds, imported under another name (#998 r1)": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n",
                "tests/deco.py": "import pytest\n\nfx = pytest.fixture\n",
                "tests/helpers.py": "import pytest\nfrom deco import fx as wrap\n\n\n"
                "def _impl(dep):\n    " + CHECK + "\n\nguard = wrap(autouse=True)(_impl)\n"}),
    "a re-exported alias reached through a star import (#998 r1)": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n",
                "tests/deco.py": "import pytest\n\nfx = pytest.fixture\n",
                "tests/mid.py": "from deco import fx as g  # noqa: F401\n",
                "tests/helpers.py": "import pytest\nfrom mid import *  # noqa: F403\n\n\n"
                "def _impl(dep):\n    " + CHECK + "\n\nguard = g(_impl)\n"}),
    "a factory whose decorator names fixture in a longer string (#998 r1, main's rule)": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n", "tests/helpers.py":
                "import pytest\n\n\ndef make():\n    @getattr(pytest, 'fixture '.strip())\n"
                "    def guard(dep):\n        " + CHECK.replace("\n    ", "\n        ")
                + "    return guard\n\n\nguard = make()\n"}),
    "a fixture factory decorator from another module": (
        "dep", {ABOVE: "from helpers import guard  # noqa: F401\n", "tests/deco.py":
                "import pytest\n\n\ndef make(fn):\n    return pytest.fixture(autouse=True)(fn)\n",
                "tests/helpers.py": "import pytest\nfrom deco import make\n\n\n@make\n"
                "def guard(dep):\n    " + CHECK}),
}  # fmt: skip
MISSED_ABOVE = {  # name: (taken name, the conftest above)
    "a name= from a variable": ("alias", NAME_VAR),
    "a name= in a call that registers it": ("alias", CALL_NAME),
    "a name= in a two-step call": ("alias", TWO_STEP_NAME),
    "a fixture defined inside a top-level if": ("guard", IF_GUARD),
}
MISSED_SAME_FILE = {  # name: (taken name, the base version of the same conftest)
    "a name= from a variable": ("alias", NAME_VAR),
    "a name= in a call that registers it": ("alias", CALL_NAME),
    "a fixture defined inside a top-level if": ("guard", IF_GUARD),
}


@pytest.mark.parametrize("name", list(MISSED_IMPORTED))
def test_a_fixture_an_imported_module_registers_another_way_is_taken(repo, name):
    taken, base_files = MISSED_IMPORTED[name]
    files = {**base_files, ABOVE: PATH + base_files.get(ABOVE, "")}
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({taken}\\)"):
        _check(repo, {CONFTEST: FIXTURE.format(taken)}, path=CONFTEST, base_files=files)


@pytest.mark.parametrize("name", list(MISSED_ABOVE))
def test_a_fixture_a_conftest_above_registers_another_way_is_taken(repo, name):
    taken, above = MISSED_ABOVE[name]
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({taken}\\)"):
        _check(repo, {CONFTEST: FIXTURE.format(taken)}, path=CONFTEST,
               base_files={ABOVE: above})  # fmt: skip


@pytest.mark.parametrize("name", list(MISSED_SAME_FILE))
def test_a_fixture_the_same_conftest_registers_another_way_is_taken(repo, name):
    taken, base = MISSED_SAME_FILE[name]
    new = base + f"\n\n@pytest.fixture\ndef {taken}():\n    return None\n"
    with pytest.raises(dt.TestCommitRejected, match=f"base test requests \\({taken}\\)"):
        _check(repo, {CONFTEST: new}, path=CONFTEST, base_files={CONFTEST: base})


DECO = "import pytest\n\nfx = pytest.fixture\n"
# registers "alias" through the alias: a literal name= of any call is read anyway, a variable is
# read only for a call of fixture or an alias of it
WRAPPED = "CUSTOM = 'alias'\nX = wrap(_impl, name=CUSTOM, autouse=True)\n"


def test_an_alias_another_module_exports_registers_a_name_above(repo):
    """#998 r1: a conftest above registers a name= through an alias a module it imports binds."""
    above = PATH + "from deco import fx as wrap\n" + IMPL + WRAPPED
    with pytest.raises(dt.TestCommitRejected, match="base test requests \\(alias\\)"):
        _check(repo, {CONFTEST: FIXTURE.format("alias")}, path=CONFTEST,
               base_files={ABOVE: above, "tests/deco.py": DECO})  # fmt: skip


def test_an_alias_another_module_exports_registers_a_name_in_the_same_conftest(repo):
    base = PATH + "from deco import fx as wrap\n" + IMPL + WRAPPED
    new = base + "\n\n@pytest.fixture\ndef alias():\n    return None\n"
    with pytest.raises(dt.TestCommitRejected, match="base test requests \\(alias\\)"):
        _check(repo, {CONFTEST: new}, path=CONFTEST,
               base_files={CONFTEST: base, "tests/sub/deco.py": DECO})  # fmt: skip


def test_an_alias_a_conftest_above_binds_is_followed_into_an_imported_module(repo):
    """Static reading: the alias is bound in the conftest above, not in an imported module."""
    above = PATH + DECO + "from helpers import X  # noqa: E402,F401\n"
    helpers = "from conftest import fx as wrap\n" + IMPL + WRAPPED
    with pytest.raises(dt.TestCommitRejected, match="base test requests \\(alias\\)"):
        _check(repo, {CONFTEST: FIXTURE.format("alias")}, path=CONFTEST,
               base_files={ABOVE: above, "tests/helpers.py": helpers})  # fmt: skip


def test_bound_reads_every_block_that_runs_at_module_level():
    tree = ast.parse(
        "try:\n    import a\nexcept E:\n    def b(): pass\nelse:\n    c = 1\nfinally:\n    d = 2\n"
        "with m:\n    e = 3\nfor i in x:\n    f = 4\nelse:\n    g = 5\nwhile t:\n    h = 6\n"
        "match v:\n    case 1:\n        j = 7\ndef k():\n    inner = 8\nclass L:\n    attr = 9\n"
    )
    assert bound(tree) == {"a", "b", "c", "d", "e", "f", "g", "h", "j", "k", "L"}


def test_what_a_fixture_call_registers_is_read():
    tree = ast.parse(
        "import pytest\nfrom pytest import fixture as fx\nf = pytest.fixture\ng = f\n"
        "X = g(autouse=True)(_a)\nY: object = fx(_b)\nZ = pytest.fixture(_c, name='z')\n"
        "W = other(_d)\nthread(name='t', number=1)\nthread(name=1)\n"
        "H = frozenset('pytest_fixture_setup'.split())\nG = getattr(pytest, 'fixture')(_e)\n"
    )
    assert _registered(tree) == {"X", "_a", "Y", "_b", "Z", "_c", "z", "t", "G", "_e"}
    routed = ast.parse(
        "@app.route('/')\ndef index(req):\n    pass\n\n\ndef plain(x):\n    pass\n\n\n"
        "class C:\n    @property\n    def p(self):\n        pass\n\n"
        "    @pytest.fixture\n    def m(self, dep):\n        pass\n"
    )  # any module-level decorator may be a fixture factory; a method needs a fixture one
    assert _fixtures(routed) == {"index", "req", "m", "self", "dep"}


STRING_NAME = (
    "import pytest\n\nN = 'alias'\nM = 'other'\n\n\n@pytest.fixture(name=N)\ndef g(): pass\n"
)
UNREADABLE = {  # name: a module whose fixture's name= cannot be read
    "an imported name": "from consts import N\n",
    "a name also bound by a call": "N = 'a'\nN = make()\n",
    "a name a star import may bind": "N = 'a'\nfrom consts import *\n",
    "a name bound by a loop": "N = 'a'\nfor N in names:\n    pass\n",
    "a name bound as a parameter": "N = 'a'\ndef h(N='b'): pass\n",
    "a name bound by a def": "N = 'a'\ndef N(): pass\n",
    "a name bound by two targets": "N = M = 'a'\n",
    "a name never bound": "",
    "an expression": "N = 'al'\nP = N + 'ias'\n",
}


def test_a_name_from_a_string_constant_counts_every_string_of_the_module():
    assert {"alias", "other"} <= _registered(ast.parse(STRING_NAME))
    assert "t" in _registered(ast.parse("thread(name=n)\nthread(name='t')\n"))  # not a fixture


@pytest.mark.parametrize("name", list(UNREADABLE))
def test_a_fixture_name_that_cannot_be_read_is_refused(name):
    use = "N" if name != "an expression" else "N + 'ias'"
    text = f"import pytest\n{UNREADABLE[name]}\n\n@pytest.fixture(name={use})\ndef g(): pass\n"
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(text))
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(
            ast.parse("import pytest\nopts = {'name': 'x'}\nY = pytest.fixture(**opts)(f)\n")
        )


def test_exported_follows_re_exports_under_any_name():
    trees = [ast.parse(t) for t in (
        "import pytest\nfx = pytest.fixture\n", "from a import fx as g\n", "from b import g as h\n",
        "from c import h\nk = h\n", "from x import other\n",
    )]  # fmt: skip
    assert {"fx", "g", "h", "k"} <= exported(trees) and "other" not in exported(trees)
