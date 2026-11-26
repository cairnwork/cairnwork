"""#1833: a test class whose base is imported from another module gets the right test ids.

The old fallback for a base it could not resolve pulled in the methods of every earlier class of
the file (ids pytest never has: a false DEFERRED_TESTS_NOT_RUN), and a class with such a base was
not read as a test class at all (a false "tests do not fail on base")."""

import ast

import pytest

from cairnwork.deferred_nodes import test_functions as functions_of

EARLIER = (
    "import unittest\n\n\nclass P(unittest.TestCase):\n    def test_p(self):\n        pass\n\n"
)
IMPORTS = {
    "from-import": ("from helpers import Base\n", "Base"),
    "from-import-alias": ("from helpers import Base as B\n", "B"),
    "relative-import": ("from .helpers import Base\n", "Base"),
    "module-attribute": ("import helpers\n", "helpers.Base"),
    "dotted-alias": ("import helpers.sub as hs\n", "hs.Base"),
}
CLASS_I = "class I({base}):\n    def test_i(self):\n        pass\n"


def _ids(src: str) -> set[str]:
    found, _ = functions_of(ast.parse(src))
    return set(found)


@pytest.mark.parametrize("shape", IMPORTS)
def test_an_imported_base_does_not_pull_in_an_earlier_classs_methods(shape):
    imp, base = IMPORTS[shape]
    ids = _ids(imp + EARLIER + CLASS_I.format(base=base))
    assert "I::test_p" not in ids and "P::test_p" in ids


@pytest.mark.parametrize("shape", IMPORTS)
def test_a_lone_class_with_an_imported_base_is_a_test_class(shape):
    imp, base = IMPORTS[shape]
    assert _ids(imp + CLASS_I.format(base=base)) == {"I::test_i"}


@pytest.mark.parametrize("shape", IMPORTS)
def test_both_classes_get_exactly_their_own_ids(shape):
    imp, base = IMPORTS[shape]
    assert _ids(imp + EARLIER + CLASS_I.format(base=base)) == {"P::test_p", "I::test_i"}


def test_a_same_file_base_still_hands_down_its_tests():
    src = "class Base:\n    def test_b(self):\n        pass\n\n\nclass TestC(Base):\n    pass\n"
    assert "TestC::test_b" in _ids(src)


def test_a_test_class_with_an_imported_base_still_inherits_nothing_from_earlier_classes():
    src = "from helpers import Base\n\n\nclass TestA:\n    def test_a(self):\n        pass\n\n\n"
    src += "class TestB(Base):\n    def test_b(self):\n        pass\n"
    assert _ids(src) == {"TestA::test_a", "TestB::test_b"}


def test_a_class_with_no_base_and_a_plain_name_is_not_a_test_class():
    assert _ids("class Helper:\n    def test_h(self):\n        pass\n") == set()


def test_a_base_rebound_by_assignment_still_gets_the_cautious_fallback():
    src = "class TestA:\n    def test_a(self):\n        pass\n\n\nBase = make()\n\n\n"
    src += "class TestB(Base):\n    def test_b(self):\n        pass\n"
    assert {"TestB::test_a", "TestB::test_b"} <= _ids(src)


PRE = (
    "import unittest\nfrom helpers import Base\n\n\nclass P(unittest.TestCase):\n"
    "    def test_p(self):\n        pass\n\n"
)
TAIL = "\nclass I(Base):\n    def test_i(self):\n        pass\n"
REBINDS = {
    "assign": "Base = make(P)\n",
    "if-assign": "if True:\n    Base = P\n",
    "for-target": "for Base in (P,):\n    pass\n",
    "walrus": "(Base := P)\n",
    "annotated": "Base: type = make(P)\n",
    "with-as": "with ctx() as Base:\n    pass\n",
    "global-write": "def f():\n    global Base\n    Base = P\nf()\n",
    "class-in-if": "if True:\n    class Base(P):\n        pass\n",
    "class-in-try": "try:\n    class Base(P):\n        pass\nexcept Exception:\n    pass\n",
    "class-in-with": "with ctx():\n    class Base(P):\n        pass\n",
    "class-in-while": "while True:\n    class Base(P):\n        pass\n    break\n",
    "class-in-match": "match 0:\n    case _:\n        class Base(P):\n            pass\n",
    "match-capture": "match P:\n    case Base:\n        pass\n",
    "match-as": "match P:\n    case object() as Base:\n        pass\n",
    "import-in-if": "if True:\n    from test_self import P as Base\n",
    "second-import": "from test_self import P as Base\n",
    "def-rename": "def Base():\n    pass\n",
    "async-def-rename": "async def Base():\n    pass\n",
    "except-target": "try:\n    pass\nexcept Exception as Base:\n    pass\n",
    "match-star": "match [P]:\n    case [*Base]:\n        pass\n",
    "match-mapping-rest": "match {}:\n    case {**Base}:\n        pass\n",
    # writes that no binding statement shows: the file falls back to the cautious reading
    "namespace-dict": "globals()['Base'] = P\n",
    "setattr-module": "import sys\nsetattr(sys.modules[__name__], 'Base', P)\n",
    "sys-modules-attribute": "import sys\nsys.modules[__name__].Base = P\n",
    "exec-string": "exec('Base = P')\n",
    "later-star-import": "from other import *\n",
    "module-dict": "__dict__['Base'] = P\n",
}


@pytest.mark.parametrize("middle", REBINDS)
def test_an_imported_name_rebound_anywhere_keeps_the_cautious_fallback(middle):
    assert "I::test_p" in _ids(PRE + REBINDS[middle] + TAIL)


def test_a_name_used_only_as_a_base_stays_import_bound():
    ids = _ids(PRE + TAIL)
    assert "I::test_p" not in ids and "I::test_i" in ids


@pytest.mark.parametrize("middle", ["print(Base)\n", "Base.attr = 1\n", "x = Base\n"])
def test_any_other_use_of_the_name_keeps_the_cautious_fallback(middle):
    """#1840 head5: only a name that appears nowhere but in a base list is read narrowly."""
    assert "I::test_p" in _ids(PRE + middle + TAIL)


def test_an_import_of_a_class_defined_in_the_same_file_is_not_import_bound():
    """A module importing itself: ``from <this file> import P as Base`` names the earlier class."""
    src = "import unittest\nfrom test_self import P as Base\n\n\nclass P(unittest.TestCase):\n"
    src += "    def test_p(self):\n        pass\n" + TAIL
    assert "I::test_p" in _ids(src)
