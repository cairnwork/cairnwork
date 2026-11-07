"""#982 / #988 follow-up: ways of registering a fixture that conftest_names reads since #982.

Each case is a base file pair whose fixture pytest 9 registers and a nested override of a name it
depends on (or of its registered name) turns off; conftest_names takes that name. Plain pytest
confirms every form registers the fixture and that the nested override disables it. They were
strict xfails on main (pre-existing before #988 too)."""

import ast
import sys

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt
from cairnwork.conftest_names import _fixtures, bound
from cairnwork.fixture_forms import _aliases
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


# #998 r2 (Claude B4): a test file binds ``run`` to what a "fixture"-named loader returns, and a
# helper imports an unrelated ``run`` and calls it with **kwargs. An alias is read in its own
# module only, so the helper's ``run`` is no alias and the benign change is accepted.
UNRELATED = {
    ABOVE: PATH + "from helpers import sh  # noqa: F401\n",
    "tests/helpers.py": "from subprocess import run\n\n\n"
    "def sh(*a, **kw):\n    return run(*a, **kw)\n",
    "tests/sub/test_data.py": "def load_fixture(n):\n    return n\n\n\nrun = load_fixture('x')\n",
}


def test_an_alias_of_another_module_is_not_read_into_an_unrelated_import(repo):
    _check(repo, {CONFTEST: FIXTURE.format("fresh")}, path=CONFTEST, base_files=UNRELATED)


EXACT_UNREADABLE = {  # name: a call of fixture itself whose name= cannot be read (refused)
    "an imported alias": "from pytest import fixture as fx\nfx(name=make())(g)\n",
    "an alias of an alias": "from pytest import fixture as fx\nf = fx\n(h := f)(name=make())(g)\n",
    "an annotated alias": "import pytest\nf: object = pytest.fixture\nf(name=make())(g)\n",
    "getattr": "import pytest\ngetattr(pytest, 'fixture')(**opts)(g)\n",
    "a tuple-target alias": "import pytest\nfx, n = pytest.fixture, 1\nfx(name=make())(g)\n",
    "a chained-assignment alias": "import pytest\na = fx = pytest.fixture\nfx(name=make())(g)\n",
    # #1019 r1 (Codex): a module defining its own fixture is not exempt where it registers one
    "a module's own fixture, called with an imported name": "import pytest\nfrom c import N\n\n\n"
    "def fixture(fn=None, **kw):\n    return pytest.fixture(fn, **kw)\n\n\n"
    "@fixture(name=N)\ndef g():\n    pass\n",
    "a module's own fixture, called in a function with an imported name": "import pytest\n"
    "from c import N\n\n\ndef fixture(fn=None, **kw):\n    return pytest.fixture(fn, **kw)\n\n\n"
    "def make(unrelated):\n    return fixture(name=N)\n",
    "a wrapper forwarding its parameter, in a module without its own fixture": "import pytest\n\n\n"
    "def make(n):\n    return pytest.fixture(name=n)\n",
}
NOT_EXACT = {  # name: a module whose call only mentions the word; adds nothing, as on main
    "a helper of a module named fixtures": "from _pytest import fixtures\n\n\n"
    "def f(cls):\n    fixtures.register_fixture(name=f'_setup_{cls.__name__}', func=cls)\n",
    "a call through a name bound from the word": "info = node._fixtureinfo\ninfo.prune(**kwargs)\n",
    "pytest's own fixture, calling itself": "def fixture(fn=None, *, name=None):\n"
    "    return fixture(name=name)(fn) if fn else None\n",
}


@pytest.mark.parametrize("name", list(EXACT_UNREADABLE))
def test_an_unreadable_name_of_fixture_itself_is_refused(name):
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(EXACT_UNREADABLE[name]))


@pytest.mark.parametrize("name", list(NOT_EXACT))
def test_an_unreadable_name_of_a_call_that_only_mentions_the_word_adds_nothing(name):
    """#998 r3 (Claude): pytest's own source has such calls; refusing them refused every conftest
    change of that repository (main refuses none)."""
    assert _registered(ast.parse(NOT_EXACT[name])) <= {"f", "cls", "fn", "name"}


def test_an_annotated_string_constant_name_is_read():
    """#1019 r1 (Codex): ``N: str = "alias"`` is as readable as ``N = "alias"``."""
    text = "import pytest\nN: str = 'alias'\n\n\n@pytest.fixture(name=N)\ndef g(): pass\n"
    assert "alias" in _registered(ast.parse(text))


def test_a_deep_conditional_chain_is_read_without_recursion():
    """#998 r3 (Claude): reading a deep chain must not raise RecursionError. 800 parses on every
    supported Python (3000 does not on 3.11, #1019 CI); the read runs with only 100 frames to
    spare, so a reader that recursed once per level would fail here."""
    chain = "import pytest\nf = " + " if c else ".join(["pytest.fixture"] * 800) + "\n"
    tree, depth, frame = ast.parse(chain), 0, sys._getframe()
    while frame is not None:
        depth, frame = depth + 1, frame.f_back
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(depth + 100)
    try:
        aliases, names = _aliases(tree), _registered(tree)
    finally:
        sys.setrecursionlimit(limit)
    assert "f" in aliases and names == set()
