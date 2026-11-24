"""#1604: a forwarded fixture name= is exempt only inside the module's own top-level def fixture
(pytest's factory). Every other wrapper is refused, whatever its call sites pass (whitelist: call
sites are never read, so no spelling of a dynamic lookup can matter)."""

import ast

import pytest
from test_conftest_names import _check

import cairnwork.deferred_tests as dt
from cairnwork.fixture_forms import registered as _registered

HEAD = "import pytest\n\n\ndef fixture(fn=None, **kw):\n    return pytest.fixture(fn, **kw)\n\n\n"
ABOVE = "tests/conftest.py"
CONFTEST = "tests/sub/conftest.py"
WRAP = "def mk(n):\n    return fixture(name=n)\n\n\n"

REFUSED = {  # shape: module text after HEAD
    "a variable": WRAP + "n = make()\nmk(n)\n",
    "an f-string": WRAP + "mk(f'a{x}')\n",
    "a nested call": WRAP + "mk(str(x))\n",
    "a conditional expression": WRAP + "mk('a' if c else 'b')\n",
    "*args at the call": WRAP + "mk(*names)\n",
    "**kwargs at the call": WRAP + "mk(**opts)\n",
    "a wrapper nobody here calls": WRAP,
    "a wrapper called from another module only": WRAP + "# mk('taken') lives elsewhere\n",
    "a wrapper referenced as a value": WRAP + "alias = mk\nalias('taken')\n",
    "a wrapper used as a decorator": WRAP + "@mk\ndef g(): pass\n",
    "an attribute call whose argument is not a literal": WRAP + "h.mk(n)\n",
    "a non-literal default": "def mk(n=make()):\n    return fixture(name=n)\n\n\nmk()\n",
    "a (*a, **k) wrapper": "def mk(*a, **k):\n    return fixture(*a, **k)\n\n\nmk(name='taken')\n",
    "a **kwargs wrapper": "def mk(**kw):\n    return fixture(**kw)\n\n\nmk(name='taken')\n",
    "a second hop with a non-literal": WRAP + "def hop(m):\n    return mk(m)\n\n\nhop(x)\n",
    "a second hop nobody calls": WRAP + "def hop(m):\n    return mk(m)\n\n\n",
    "an attribute fixture call": "def mk(n):\n    return pytest.fixture(name=n)\n\n\nmk(x)\n",
    "an aliased fixture call": "fx = fixture\n\n\ndef mk(n):\n    return fx(name=n)\n\n\nmk(x)\n",
    # r1 (review desk c): the wrapper rebinds the parameter before it forwards it
    "a parameter reassigned": (
        "def mk(n):\n    n = compute()\n    return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter augmented": "def mk(n):\n    n += x\n    return fixture(name=n)\n\n\nmk('a')\n",
    "a parameter bound by a loop": (
        "def mk(n):\n    for n in xs:\n        return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter bound by a comprehension": (
        "def mk(n):\n    return [fixture(name=n) for n in xs]\n\n\nmk('a')\n"
    ),
    "a parameter bound by walrus": (
        "def mk(n):\n    (n := f())\n    return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter bound by with": (
        "def mk(n):\n    with c() as n:\n        return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter bound by except": (
        "def mk(n):\n    try:\n        pass\n    except E as n:\n"
        "        return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter declared global": (
        "def mk(n):\n    global n\n    return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter bound by a nested def": (
        "def mk(n):\n    def n(): pass\n    return fixture(name=n)\n\n\nmk('a')\n"
    ),
    "a parameter bound by a match": (
        "def mk(n):\n    match v:\n        case n:\n"
        "            return fixture(name=n)\n\n\nmk('a')\n"
    ),
    # the wrapper itself is rebound or reached dynamically
    "a wrapper reassigned": WRAP + "mk('a')\nmk = other\nmk(x)\n",
    "a wrapper deleted": WRAP + "mk('a')\ndel mk\n",
    "a wrapper imported over": WRAP + "mk('a')\nfrom o import m as mk\n",
    "a wrapper defined twice": WRAP + WRAP + "mk('a')\n",
    "a wrapper called through getattr": WRAP + "mk('a')\ngetattr(M, 'mk')(x)\n",
    "a wrapper called through globals": WRAP + "mk('a')\nglobals()['mk'](x)\n",
    "a wrapper aliased by an attribute": WRAP + "mk('a')\nalias = h.mk\nalias(x)\n",
    "a wrapper called through a computed globals key": WRAP
    + "mk('safe')\nglobals()['m' + 'k'](x)\n",
    "a wrapper called through a computed vars key": WRAP + "mk('safe')\nvars()['m' + 'k'](x)\n",
    "a wrapper called through attrgetter": WRAP
    + "mk('safe')\nimport operator\noperator.attrgetter('m' + 'k')(M)('taken')\n",
    "a wrapper called through methodcaller": WRAP
    + "mk('safe')\nimport operator\noperator.methodcaller('m' + 'k', 'taken')(M)\n",
    "a wrapper called through __getattribute__": WRAP
    + "mk('safe')\nM.__getattribute__('m' + 'k')('taken')\n",
    "a wrapper called through import_module": WRAP
    + "mk('safe')\nimportlib.import_module(__name__).__getattribute__('m' + 'k')('taken')\n",
    "a wrapper called through a computed callee": WRAP + "mk('safe')\nget()('taken')\n",
    "a wrapper called through a subscripted callee": WRAP + "mk('safe')\ntable[k]('taken')\n",
    "a wrapper reached through an aliased getattr": WRAP
    + "import sys\nmk('safe')\nlookup = getattr\n"
    + "hidden = lookup(sys.modules[__name__], 'm' + 'k')\nhidden('hidden')\n",
    "a wrapper reached through builtins.getattr": WRAP
    + "import builtins\nmk('safe')\nbuiltins.getattr(M, 'm' + 'k')('hidden')\n",
    "a wrapper called through a computed getattr": WRAP + "mk('safe')\ngetattr(M, 'm' + 'k')(x)\n",
    "a wrapper called through __dict__": WRAP + "mk('safe')\nM.__dict__['m' + 'k'](x)\n",
    "a wrapper aliased": WRAP + "mk('a')\nalias = mk\nalias(x)\n",
    # defaults and call shapes
    "a name default": "def mk(n=NAME):\n    return fixture(name=n)\n\n\nmk()\n",
    "a None default": "def mk(n=None):\n    return fixture(name=n)\n\n\nmk()\n",
    "a keyword-only non-literal default": (
        "def mk(*, n=NAME):\n    return fixture(name=n)\n\n\nmk()\n"
    ),
    "a star call of a defaulted parameter": (
        "def mk(a, m='x'):\n    return fixture(name=m)\n\n\nmk(*args)\n"
    ),
    "a **opts call of a defaulted parameter": (
        "def mk(n='x'):\n    return fixture(name=n)\n\n\nmk(**opts)\n"
    ),
    "a name default that is a parameter where it is called": (
        "def mk(n=NAME):\n    return fixture(name=n)\n\n\n"
        "def outer(NAME):\n    return mk()\n\n\nouter('a')\n"
    ),
    "None passed": WRAP + "mk(None)\n",
    "an int passed": WRAP + "mk(1)\n",
    "bytes passed": WRAP + "mk(b'a')\n",
    "a **kwargs parameter named n": "def mk(**n):\n    return fixture(name=n)\n\n\nmk(n='taken')\n",
    "a staticmethod wrapper": (
        "class C:\n    @staticmethod\n    def mk(n):\n"
        "        return fixture(name=n)\n\n\nC.mk('a')\n"
    ),
    "a lambda wrapper": "mk = lambda n: fixture(name=n)\nmk('taken')\n",
    "a method wrapper": "class C:\n    def mk(self, n):\n        return fixture(name=n)\n\n\n"
    "C().mk('taken')\n",
    "one literal and one variable call": WRAP + "mk('a')\nmk(x)\n",
}


@pytest.mark.parametrize("shape", list(REFUSED))
def test_an_unresolvable_forwarded_name_is_refused(shape):
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(HEAD + REFUSED[shape]))


BYPASS = {  # r5 (Claude): reach the wrapper by a computed name, with no `getattr` token; moot now
    "a builtins alias": (
        "from builtins import getattr as g\nh = g(sys.modules[__name__], 'm' + 'k')\n"
    ),
    "__globals__": "h = fixture.__globals__['m' + 'k']\n",
    "a frame's globals": "d = sys._getframe().f_globals\nh = d['m' + 'k']\n",
    "getattr_static": "h = inspect.getattr_static(sys.modules[__name__], 'm' + 'k')\n",
    "getmembers": "d = dict(inspect.getmembers(sys.modules[__name__]))\nh = d['m' + 'k']\n",
    "a match class pattern": "match sys.modules[__name__]:\n    case object(mk=h):\n        pass\n",
    "a self-import alias": "from helpers import mk as h\n",
    "__builtins__": "g = __builtins__['get' + 'attr']\nh = g(sys.modules[__name__], 'm' + 'k')\n",
    "resolve_name": "h = pkgutil.resolve_name('helpers:' + 'm' + 'k')\n",
}


@pytest.mark.parametrize("shape", list(BYPASS))
def test_a_computed_lookup_of_a_wrapper_cannot_register_a_hidden_name(shape):
    text = HEAD + WRAP + "mk('safe')\n" + BYPASS[shape] + "h('hidden')\n"
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(text))


LITERAL = {  # a wrapper is refused even when every call site is a literal (not read any more)
    "a literal call": WRAP + "mk('taken')\n",
    "a literal default": "def mk(n='taken'):\n    return fixture(name=n)\n\n\nmk()\n",
    "a keyword-only literal": "def mk(*, n):\n    return fixture(name=n)\n\n\nmk(n='taken')\n",
    "a second hop with a literal": WRAP + "def hop(m):\n    return mk(m)\n\n\nhop('taken')\n",
    "a nested wrapper": (
        "def outer():\n    def mk(n):\n        return fixture(name=n)\n    mk('a')\n"
    ),
    "a lambda wrapper": "mk = lambda n: fixture(name=n)\nmk('taken')\n",
    "a nested function inside fixture": (
        "def fixture(fn=None, *, name=None):\n    def inner():\n"
        "        return fixture(name=name)\n    return inner\n"
    ),
}


@pytest.mark.parametrize("shape", list(LITERAL))
def test_a_wrapper_is_refused_even_with_literal_call_sites(shape):
    text = LITERAL[shape]
    text = text if text.lstrip().startswith("def fixture") else HEAD + text
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(text))


def test_pytests_own_factory_keeps_its_exemption():
    text = "def fixture(fn=None, *, name=None, **kw):\n    return fixture(name=name, **kw)(fn)\n"
    assert _registered(ast.parse(text)) <= {"fixture", "fn", "name", "kw"}


PRE = "import pytest\n\n\n"
FACTORY = "def fixture(fn=None, *, name=None):\n    return pytest.fixture(fn, name=name)\n"
NOT_TOP_LEVEL = {  # a `def fixture` that is not the module's top-level one is not the exemption
    "a def inside a function": "def outer():\n"
    + "".join("    " + ln + "\n" for ln in FACTORY.splitlines()),
    "a method of a class": (
        "class C:\n    def fixture(self, fn=None, *, name=None):\n"
        "        return pytest.fixture(fn, name=name)\n"
    ),
    "a top-level one plus a method that forwards another parameter": (
        "def fixture(fn=None, **kw):\n    return pytest.fixture(fn, **kw)\n\n\n"
        "class C:\n    def fixture(self, n):\n        return pytest.fixture(name=n)\n"
    ),
    "an async def inside an async function": (
        "async def outer():\n    async def fixture(name=None):\n"
        "        return pytest.fixture(name=name)\n"
    ),
}
TOP_LEVEL = {  # the exemption survives where the `def fixture` still runs at module level
    "a plain top-level def with **kw": (
        "def fixture(fn=None, *, name=None, **kw):\n"
        "    return pytest.fixture(fn, name=name, **kw)\n"
    ),
    "a def under an if": "if True:\n" + "".join("    " + ln + "\n" for ln in FACTORY.splitlines()),
    "a def in a try's else": (
        "try:\n    pass\nexcept E:\n    pass\nelse:\n"
        + "".join("    " + ln + "\n" for ln in FACTORY.splitlines())
    ),
}


@pytest.mark.parametrize("shape", list(NOT_TOP_LEVEL))
def test_only_the_top_level_def_fixture_is_exempt(shape):
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _registered(ast.parse(PRE + NOT_TOP_LEVEL[shape]))


@pytest.mark.parametrize("shape", list(TOP_LEVEL))
def test_a_def_fixture_that_runs_at_module_level_keeps_the_exemption(shape):
    _registered(ast.parse(PRE + TOP_LEVEL[shape]))


def _helper(body: str) -> dict[str, str]:
    return {
        ABOVE: "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
        "from helpers import *  # noqa: F403\n",
        "tests/helpers.py": HEAD + body,
    }


CANDIDATE = "import pytest\n\n\n@pytest.fixture\ndef taken():\n    return 1\n"
OTHER = "import pytest\n\n\n@pytest.fixture\ndef other():\n    return 1\n"


@pytest.mark.parametrize("call", ["mk('taken')\n", "mk(make())\n", ""])
def test_a_wrapper_in_the_base_refuses_every_candidate_end_to_end(repo, call):
    with pytest.raises(dt.TestCommitRejected, match="not a string literal"):
        _check(repo, {CONFTEST: OTHER}, path=CONFTEST, base_files=_helper(WRAP + call))


def test_a_candidate_is_accepted_when_the_base_has_no_wrapper(repo):
    _check(repo, {CONFTEST: OTHER}, path=CONFTEST, base_files=_helper("x = 1\n"))
