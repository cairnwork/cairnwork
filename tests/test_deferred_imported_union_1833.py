"""#1833: a test class with an imported base keeps every id the cautious reading gives.

The ids of a class with an imported base are the union of main's reading (every earlier class
of the file may be the base: none is dropped) and the class's own, which are targets because the
class is read as a possible test case. So no run-time rewrite of the base (``__bases__``, a
renamed ``setattr``, a decorator, a metaclass, ``__init_subclass__``, a write through the
subclass) can leave fewer ids than main gives: the property needs no list of such writes. Every
shape that three earlier review rounds used to defeat a narrower reading is pinned here."""

import ast

import pytest

from cairnwork import deferred_nodes
from cairnwork.deferred_nodes import test_functions as functions_of

HEAD = "import sys\nimport unittest\nfrom unittest import mock\n"
P = "class P(unittest.TestCase):\n    def test_p(self):\n        pass\n\n"
IMPORTS = {
    "from-import": ("from helpers import Base\n", "Base"),
    "alias": ("from helpers import Base as B\n", "B"),
    "relative": ("from .helpers import Base\n", "Base"),
    "module-attribute": ("import helpers\n", "helpers.Base"),
    "dotted-alias": ("import helpers.sub as hs\n", "hs.Base"),
}
TAIL = "\n\nclass {name}({base}):\n    def test_i(self):\n        pass\n"


def _ids(src: str) -> set[str]:
    return set(functions_of(ast.parse(src))[0])


def _main_reading(src: str, monkeypatch) -> set[str]:
    """main's ids: no base counts as imported, so the cautious fallback reads every base."""
    with monkeypatch.context() as m:
        m.setattr(deferred_nodes, "imported_names", lambda tree: frozenset(), raising=False)
        return _ids(src)


SHAPES = {
    "AFTER_IMPORT-attr-store-same-name": ("class NS:\n    pass\n\n\nNS.Base = P\n"),
    "AFTER_IMPORT-b-globals-subscript": ("def _f():\n    pass\n\n\n_f.__globals__['Base'] = P\n"),
    "AFTER_IMPORT-builtins-setattr": ("import builtins\nbuiltins.setattr(sys, 'x', 1)\n"),
    "AFTER_IMPORT-c-getframe": ("sys._getframe().f_globals['Base'] = P\n"),
    "AFTER_IMPORT-currentframe": ("import inspect\ninspect.currentframe().f_globals['Base'] = P\n"),
    "AFTER_IMPORT-d-modules-name": ("from sys import modules\nmodules[__name__].Base = P\n"),
    "AFTER_IMPORT-delattr": ("delattr(sys, 'Base')\n"),
    "AFTER_IMPORT-dotted-string": ("TARGET = 'test_self.Base'\n"),
    "AFTER_IMPORT-e-self-import": ("import test_self as me\nme.Base = P\n"),
    "AFTER_IMPORT-f-monkeypatch": ("pytest.MonkeyPatch().setattr('test_self.Base', P)\n"),
    "AFTER_IMPORT-frame-update": ("sys._getframe().f_globals.update(Base=P)\n"),
    "AFTER_IMPORT-g-mock-patch-start": ("mock.patch('test_self.Base', P).start()\n"),
    "AFTER_IMPORT-mock-patch-object": (
        "import helpers\nmock.patch.object(helpers, 'Base', P).start()\n"
    ),
    "AFTER_IMPORT-module-attr-after": ("import helpers\nhelpers.Base = P\n"),
    "AFTER_IMPORT-module-dict-write": ("import helpers\nhelpers.__dict__['Base'] = P\n"),
    "AFTER_IMPORT-monkeypatch-fixture": (
        "def test_mp(monkeypatch):\n    monkeypatch.setattr('helpers.Base', P)\n"
    ),
    "AFTER_IMPORT-reload": ("import importlib\nimportlib.reload(sys)\n"),
    "AFTER_IMPORT-string-name-only": ("NAMES = ['Base']\n"),
    "AFTER_IMPORT-type-call-dict": ("ns = vars(sys.modules['test_self'])\n"),
    "AFTER_IMPORT-types-module": ("import types\ntypes.SimpleNamespace(Base=P)\n"),
    "BEFORE_IMPORT-a-module-attr-before-import": ("import helpers\nhelpers.Base = P\n"),
    "OTHER_USES-alias": ("Alias = Base\n"),
    "OTHER_USES-alias-then-write": ("A = Base\nA.__bases__ = (P,)\n"),
    "OTHER_USES-annotated": ("Base: int = 1\n"),
    "OTHER_USES-argument": ("def f(x):\n    return x\n\n\nf(Base)\n"),
    "OTHER_USES-attr-del": ("del Base.test_p\n"),
    "OTHER_USES-augassign": ("Base += 1\n"),
    "OTHER_USES-bases-assign": ("Base.__bases__ = (P,)\n"),
    "OTHER_USES-bases-assign-tuple-target": ("Base.__bases__, x = (P,), 1\n"),
    "OTHER_USES-builtins-delattr-renamed": (
        "from builtins import delattr as rm\nrm(Base, 'test_x')\n"
    ),
    "OTHER_USES-builtins-setattr-renamed": (
        "from builtins import setattr as put\nput(Base, 'test_p', P.test_p)\n"
    ),
    "OTHER_USES-call": ("Base()\n"),
    "OTHER_USES-class-body-use": ("class Q:\n    Base.test_p = P.test_p\n"),
    "OTHER_USES-compare": ("x = Base is P\n"),
    "OTHER_USES-comprehension": ("[Base for _ in range(1)]\n"),
    "OTHER_USES-decorator": ("@Base\ndef dec():\n    pass\n"),
    "OTHER_USES-del-name": ("del Base\n"),
    "OTHER_USES-delattr-bound-to-name": ("rm = delattr\nrm(Base, 'x')\n"),
    "OTHER_USES-dict-write": ("Base.__dict__['test_p'] = P.test_p\n"),
    "OTHER_USES-for-target": ("for Base in [P]:\n    pass\n"),
    "OTHER_USES-fstring": ("x = f'{Base}'\n"),
    "OTHER_USES-function-param": ("def g(Base):\n    return Base\n"),
    "OTHER_USES-getattr-string": ("getattr(sys.modules[__name__], 'Base')\n"),
    "OTHER_USES-global-decl": ("def g():\n    global Base\n    Base = P\n"),
    "OTHER_USES-globals-string": ("globals()['Base'] = P\n"),
    "OTHER_USES-keyword-value": ("class Q(object, metaclass=type, flag=Base):\n    pass\n"),
    "OTHER_USES-lambda-param": ("h = lambda Base: Base\n"),
    "OTHER_USES-match-capture": ("match 1:\n    case Base:\n        pass\n"),
    "OTHER_USES-metaclass-kw": ("class M(metaclass=Base):\n    pass\n"),
    "OTHER_USES-nonlocal-decl": (
        "def g():\n    Base = 1\n\n    def h():\n        nonlocal Base\n        Base = P\n"
    ),
    "OTHER_USES-other-class-base-plus-use": ("class Q(Base):\n    X = Base\n"),
    "OTHER_USES-read": ("print(Base)\n"),
    "OTHER_USES-rebind": ("Base = P\n"),
    "OTHER_USES-setattr-attr-store": ("Base.test_p = P.test_p\n"),
    "OTHER_USES-setattr-bound-to-name": ("put = setattr\nput(Base, 'test_p', P.test_p)\n"),
    "OTHER_USES-starred": ("x = [*Base]\n"),
    "OTHER_USES-subscript": ("x = Base[0]\n"),
    "OTHER_USES-subscript-base": ("class Q(Base[int]):\n    pass\n"),
    "OTHER_USES-try-handler": ("try:\n    pass\nexcept Base:\n    pass\n"),
    "OTHER_USES-type-hint-string": ("x: 'Base' = None\n"),
    "OTHER_USES-type-setattr": ("type.__setattr__(Base, 'test_p', P.test_p)\n"),
    "OTHER_USES-vars-write": ("vars(Base)['test_p'] = P.test_p\n"),
    "OTHER_USES-walrus": ("(Base := P)\n"),
    "OTHER_USES-with-target": (
        "import contextlib\nwith contextlib.nullcontext(P) as Base:\n    pass\n"
    ),
    "OTHER_USES-write-call-inside-a-base": (
        "class Q(setattr(Base, 'test_p', P.test_p) or object):\n    pass\n"
    ),
    "REBINDS-annotated": ("Base: type = make(P)\n"),
    "REBINDS-assign": ("Base = make(P)\n"),
    "REBINDS-async-def-rename": ("async def Base():\n    pass\n"),
    "REBINDS-class-in-if": ("if True:\n    class Base(P):\n        pass\n"),
    "REBINDS-class-in-match": ("match 0:\n    case _:\n        class Base(P):\n            pass\n"),
    "REBINDS-class-in-try": (
        "try:\n    class Base(P):\n        pass\nexcept Exception:\n    pass\n"
    ),
    "REBINDS-class-in-while": ("while True:\n    class Base(P):\n        pass\n    break\n"),
    "REBINDS-class-in-with": ("with ctx():\n    class Base(P):\n        pass\n"),
    "REBINDS-def-rename": ("def Base():\n    pass\n"),
    "REBINDS-except-target": ("try:\n    pass\nexcept Exception as Base:\n    pass\n"),
    "REBINDS-exec-string": ("exec('Base = P')\n"),
    "REBINDS-for-target": ("for Base in (P,):\n    pass\n"),
    "REBINDS-global-write": ("def f():\n    global Base\n    Base = P\nf()\n"),
    "REBINDS-if-assign": ("if True:\n    Base = P\n"),
    "REBINDS-import-in-if": ("if True:\n    from test_self import P as Base\n"),
    "REBINDS-later-star-import": ("from other import *\n"),
    "REBINDS-match-as": ("match P:\n    case object() as Base:\n        pass\n"),
    "REBINDS-match-capture": ("match P:\n    case Base:\n        pass\n"),
    "REBINDS-match-mapping-rest": ("match {}:\n    case {**Base}:\n        pass\n"),
    "REBINDS-match-star": ("match [P]:\n    case [*Base]:\n        pass\n"),
    "REBINDS-module-dict": ("__dict__['Base'] = P\n"),
    "REBINDS-namespace-dict": ("globals()['Base'] = P\n"),
    "REBINDS-second-import": ("from test_self import P as Base\n"),
    "REBINDS-setattr-module": ("import sys\nsetattr(sys.modules[__name__], 'Base', P)\n"),
    "REBINDS-sys-modules-attribute": ("import sys\nsys.modules[__name__].Base = P\n"),
    "REBINDS-walrus": ("(Base := P)\n"),
    "REBINDS-with-as": ("with ctx() as Base:\n    pass\n"),
    "decorator-writes-through-bases": (
        "def deco(cls):\n    cls.__bases__[0].test_p = P.test_p\n    return cls\n"
    ),
    "init-subclass": (
        "class Hook:\n    def __init_subclass__(cls):\n        cls.__bases__[0].test_p = P.test_p\n"
    ),
    "metaclass-new": (
        "class Meta(type):\n"
        "    def __new__(m, n, b, d):\n"
        "        b[0].test_p = P.test_p\n"
        "        return super().__new__(m, n, b, d)\n"
    ),
    "mro-from-subclass": ("TestI = None\n"),
}


@pytest.mark.parametrize("imp", IMPORTS)
@pytest.mark.parametrize("name", ["I", "TestI"])
def test_a_single_class_with_an_imported_base_has_its_own_id(imp, name):
    line, base = IMPORTS[imp]
    ids = _ids(HEAD + line + TAIL.format(name=name, base=base))
    assert f"{name}::test_i" in ids


def test_a_plain_class_with_no_base_is_still_not_a_test_class():
    assert _ids("class Helper:\n    def test_h(self):\n        pass\n") == set()


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("imp", IMPORTS)
@pytest.mark.parametrize("name", ["I", "TestI"])
def test_ids_are_never_fewer_than_main_s_for_any_shape(shape, imp, name, monkeypatch):
    line, base = IMPORTS[imp]
    tail = TAIL.format(name=name, base=base)
    for src in (
        HEAD + P + line + SHAPES[shape] + tail,  # the shape after the import
        HEAD + P + SHAPES[shape] + line + tail,  # and before it
    ):
        assert _ids(src) >= _main_reading(src, monkeypatch)


@pytest.mark.parametrize("imp", IMPORTS)
def test_an_earlier_class_s_tests_stay_listed_under_the_imported_base_class(imp):
    """What main lists is kept: the base may be rebound to P at run time, and nothing here can
    prove it is not (so #1833's second symptom is not fixed statically; see the PR)."""
    line, base = IMPORTS[imp]
    ids = _ids(HEAD + P + line + TAIL.format(name="I", base=base))
    assert {"I::test_i", "P::test_p"} <= ids
