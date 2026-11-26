"""#1840 head5: an imported name is read narrowly only if it appears nowhere but in a class's
base list. Any other use of the name (read, argument, attribute access, assignment, del, alias,
global/nonlocal, a builtin write helper however renamed) falls back to main's cautious reading,
so no list of "ways to change a class" is needed."""

import ast

import pytest

from cairnwork import deferred_nodes
from cairnwork.deferred_nodes import test_functions as functions_of

HEAD = "import sys\nimport unittest\nfrom unittest import mock\n"
P = "class P(unittest.TestCase):\n    def test_p(self):\n        pass\n\n"
IMP = "from helpers import Base\n"
TAIL = "\n\nclass TestI(Base):\n    def test_i(self):\n        pass\n"


def _ids(src: str) -> set[str]:
    return set(functions_of(ast.parse(src))[0])


def _cautious(src: str, monkeypatch) -> set[str]:
    with monkeypatch.context() as m:
        m.setattr(deferred_nodes, "imported_names", lambda tree: frozenset())
        return _ids(src)


def _file(use: str) -> str:
    return HEAD + P + IMP + use + TAIL


# any use of ``Base`` other than as a class base: the result must equal the cautious reading
OTHER_USES = {
    "bases-assign": "Base.__bases__ = (P,)\n",
    "bases-assign-tuple-target": "Base.__bases__, x = (P,), 1\n",
    "builtins-setattr-renamed": (
        "from builtins import setattr as put\nput(Base, 'test_p', P.test_p)\n"
    ),
    "builtins-delattr-renamed": "from builtins import delattr as rm\nrm(Base, 'test_x')\n",
    "setattr-bound-to-name": "put = setattr\nput(Base, 'test_p', P.test_p)\n",
    "delattr-bound-to-name": "rm = delattr\nrm(Base, 'x')\n",
    "type-setattr": "type.__setattr__(Base, 'test_p', P.test_p)\n",
    "dict-write": "Base.__dict__['test_p'] = P.test_p\n",
    "vars-write": "vars(Base)['test_p'] = P.test_p\n",
    "setattr-attr-store": "Base.test_p = P.test_p\n",
    "attr-del": "del Base.test_p\n",
    "read": "print(Base)\n",
    "argument": "def f(x):\n    return x\n\n\nf(Base)\n",
    "alias": "Alias = Base\n",
    "alias-then-write": "A = Base\nA.__bases__ = (P,)\n",
    "rebind": "Base = P\n",
    "augassign": "Base += 1\n",
    "annotated": "Base: int = 1\n",
    "walrus": "(Base := P)\n",
    "del-name": "del Base\n",
    "for-target": "for Base in [P]:\n    pass\n",
    "with-target": "import contextlib\nwith contextlib.nullcontext(P) as Base:\n    pass\n",
    "global-decl": "def g():\n    global Base\n    Base = P\n",
    "nonlocal-decl": (
        "def g():\n    Base = 1\n\n    def h():\n        nonlocal Base\n        Base = P\n"
    ),
    "function-param": "def g(Base):\n    return Base\n",
    "lambda-param": "h = lambda Base: Base\n",
    "subscript": "x = Base[0]\n",
    "call": "Base()\n",
    "decorator": "@Base\ndef dec():\n    pass\n",
    "metaclass-kw": "class M(metaclass=Base):\n    pass\n",
    "other-class-base-plus-use": "class Q(Base):\n    X = Base\n",
    "globals-string": "globals()['Base'] = P\n",
    "getattr-string": "getattr(sys.modules[__name__], 'Base')\n",
    "compare": "x = Base is P\n",
    "write-call-inside-a-base": "class Q(setattr(Base, 'test_p', P.test_p) or object):\n    pass\n",
    "subscript-base": "class Q(Base[int]):\n    pass\n",
    "keyword-value": "class Q(object, metaclass=type, flag=Base):\n    pass\n",
    "class-body-use": "class Q:\n    Base.test_p = P.test_p\n",
    "comprehension": "[Base for _ in range(1)]\n",
    "try-handler": "try:\n    pass\nexcept Base:\n    pass\n",
    "match-capture": "match 1:\n    case Base:\n        pass\n",
    "type-hint-string": "x: 'Base' = None\n",
    "fstring": "x = f'{Base}'\n",
    "starred": "x = [*Base]\n",
}


@pytest.mark.parametrize("name", OTHER_USES)
def test_any_other_use_of_the_name_equals_the_cautious_reading(name, monkeypatch):
    src = _file(OTHER_USES[name])
    assert _ids(src) == _cautious(src, monkeypatch)
    if "global-decl" not in name:
        assert "TestI::test_p" in _ids(src)


MODULE_USES = {
    "bases-assign": "hs.Base.__bases__ = (P,)\n",
    "attr-store": "hs.Base = P\n",
    "read": "print(hs)\n",
    "alias": "m = hs\n",
    "setattr-renamed": "from builtins import setattr as put\nput(hs.Base, 'test_p', P.test_p)\n",
}


@pytest.mark.parametrize("name", MODULE_USES)
def test_the_module_import_form_falls_back_the_same_way(name, monkeypatch):
    src = (
        HEAD
        + P
        + "import helpers as hs\n"
        + MODULE_USES[name]
        + TAIL.replace("(Base)", "(hs.Base)")
    )
    assert _ids(src) == _cautious(src, monkeypatch)


MUST_PASS = {
    "base-only": "",
    "two-bases-only": "\n\nclass TestJ(Base):\n    def test_j(self):\n        pass\n",
    "nested-base": (
        "\n\nclass Outer:\n    class TestK(Base):\n        def test_k(self):\n            pass\n"
    ),
    "unrelated-use": "print('x')\n",
    "other-name-written": "Other = 1\nOther.__bases__ = ()\n",
}


@pytest.mark.parametrize("name", MUST_PASS)
def test_a_name_used_only_as_a_class_base_keeps_the_narrow_ids(name):
    ids = _ids(HEAD + P + IMP + MUST_PASS[name] + TAIL)
    assert "TestI::test_p" not in ids and "TestI::test_i" in ids


def test_the_module_attribute_base_form_stays_narrow():
    src = HEAD + P + "import helpers as hs\n" + TAIL.replace("(Base)", "(hs.Base)")
    ids = _ids(src)
    assert "TestI::test_p" not in ids and "TestI::test_i" in ids
