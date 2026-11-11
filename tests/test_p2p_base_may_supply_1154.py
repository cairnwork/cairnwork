"""Tests pinning _base_may_supply branch decisions for issue #1154 item 3."""

from __future__ import annotations

import ast


def test_base_object_returns_false_when_imported_testcase_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(object):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=True) is False


def test_base_object_returns_false_when_imported_testcase_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(object):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is False


def test_base_testcase_returns_false_when_imported_testcase_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(TestCase):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=True) is False


def test_base_testcase_returns_true_when_imported_testcase_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(TestCase):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_name_in_seen_returns_true_without_recursion() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(Base):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert (
        _base_may_supply(cls.bases[0], "test_x", tree, seen={"Base"}, imported_testcase=False)
        is True
    )


def test_base_name_matching_no_class_in_module_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(Missing):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_name_matching_multiple_classes_in_module_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class Base:\n    pass\n\nclass Base:\n    pass\n\nclass A(Base):\n    pass\n")
    cls = tree.body[2]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_name_matching_single_clean_class_continues_and_returns_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class Base:\n    pass\n\nclass A(Base):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is False


def test_base_class_with_metaclass_keyword_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Meta(type):\n"
        "    pass\n\n"
        "class Base(metaclass=Meta):\n"
        "    pass\n\n"
        "class A(Base):\n"
        "    pass\n"
    )
    cls = tree.body[2]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_class_with_non_pytest_decorator_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "def dataclass(cls):\n"
        "    return cls\n\n"
        "@dataclass\n"
        "class Base:\n"
        "    pass\n\n"
        "class A(Base):\n"
        "    pass\n"
    )
    cls = tree.body[2]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_class_with_pytest_mark_decorator_continues_and_returns_false_when_clean() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "import pytest\n\n@pytest.mark.slow\nclass Base:\n    pass\n\nclass A(Base):\n    pass\n"
    )
    cls = tree.body[2]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is False


def test_base_class_with_called_pytest_mark_decorator_continues_and_returns_false_when_clean() -> (
    None
):
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "import pytest\n\n"
        "@pytest.mark.timeout(10)\n"
        "class Base:\n"
        "    pass\n\n"
        "class A(Base):\n"
        "    pass\n"
    )
    cls = tree.body[2]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is False


def test_base_class_defining_init_subclass_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n    def __init_subclass__(cls):\n        pass\n\nclass A(Base):\n    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_class_defining_async_init_subclass_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n"
        "    async def __init_subclass__(cls):\n"
        "        pass\n\n"
        "class A(Base):\n"
        "    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_x", tree, set(), imported_testcase=False) is True


def test_base_class_mentioning_name_in_method_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n    def test_foo(self):\n        pass\n\nclass A(Base):\n    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_class_mentioning_name_in_assignment_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class Base:\n    test_foo = 1\n\nclass A(Base):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_class_mentioning_name_in_string_literal_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse('class Base:\n    x = "test_foo"\n\nclass A(Base):\n    pass\n')
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_class_with_nested_class_mentioning_name_does_not_count_and_returns_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n"
        "    class Nested:\n"
        "        def test_foo(self):\n"
        "            pass\n\n"
        "class A(Base):\n"
        "    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is False


def test_base_recursion_clean_grandparent_returns_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class B:\n    pass\n\nclass A(B):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is False


def test_base_recursion_grandparent_mentioning_name_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class B:\n    def test_foo(self):\n        pass\n\nclass A(B):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_recursion_cycle_terminates_via_seen_and_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(B):\n    pass\n\nclass B(A):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_unittest_testcase_attribute_returns_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("import unittest\n\nclass A(unittest.TestCase):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is False


def test_base_other_testcase_attribute_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(other.TestCase):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_unittest_other_attribute_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("import unittest\n\nclass A(unittest.Other):\n    pass\n")
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_call_expression_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(Base()):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_base_subscript_expression_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse("class A(Generic[int]):\n    pass\n")
    cls = tree.body[0]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_foo", tree, set(), imported_testcase=False) is True


def test_word_boundary_partial_suffix_match_does_not_count_and_returns_false() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n    def test_ab(self):\n        pass\n\nclass A(Base):\n    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_a", tree, set(), imported_testcase=False) is False


def test_word_boundary_match_in_attribute_access_returns_true() -> None:
    from cairnwork.p2p_collect import _base_may_supply

    tree = ast.parse(
        "class Base:\n    def f(self):\n        x.test_a = 1\n\nclass A(Base):\n    pass\n"
    )
    cls = tree.body[1]
    assert isinstance(cls, ast.ClassDef)
    assert _base_may_supply(cls.bases[0], "test_a", tree, set(), imported_testcase=False) is True
