"""#1833: a test class whose base is imported from another module gets the right test ids.

The old fallback for a base it could not resolve pulled in the methods of every earlier class of
the file (ids pytest never has: a false DEFERRED_TESTS_NOT_RUN), and a class with such a base was
not read as a test class at all (a false "tests do not fail on base")."""

import ast

import pytest

from cairnwork.deferred_nodes import test_functions as functions_of

EARLIER = "import unittest\n\n\nclass P(unittest.TestCase):\n    def test_p(self):\n        pass\n\n"
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
