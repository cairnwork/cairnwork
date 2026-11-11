"""Tests for P2P removed-test check when test name is rebound (#847)."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify


def _classify_single(base: str, test_id: str = "t.py::test_a"):
    return classify({test_id}, set(), {"t.py"}, lambda p: "x = 1\n", lambda p: base)


def test_a_test_rebound_by_assignment_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntest_a = (lambda: None)\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_assignment_to_none_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntest_a = None\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_tuple_assignment_is_dropped_not_removed():
    base = "def test_a(): assert 0\n(x, test_a) = (1, None)\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_augmented_assignment_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntest_a += 1\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_annotated_assignment_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntest_a: int = 1\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_import_alias_is_dropped_not_removed():
    base = "def test_a(): assert 0\nfrom helper import something as test_a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_for_loop_target_is_dropped_not_removed():
    base = "def test_a(): assert 0\nfor test_a in [1, 2]: pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_with_target_is_dropped_not_removed():
    base = "def test_a(): assert 0\nwith open('f') as test_a: pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_class_is_dropped_not_removed():
    base = "def test_a(): assert 0\nclass test_a: pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_class_keyword_walrus_is_dropped_not_removed():
    base = "def test_a(): assert 0\nclass B(metaclass=(test_a := type)): pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_class_keyword_without_walrus_does_not_rebind_test():
    base = "def test_a(): assert 0\nclass B(metaclass=type): pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"}


def test_a_test_rebound_by_del_is_dropped_not_removed():
    base = "def test_a(): assert 0\ndel test_a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_global_is_dropped_not_removed():
    base = "def test_a(): assert 0\nglobal test_a\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_bound_inside_if_block_is_dropped_not_removed():
    base = "def test_a(): assert 0\nif True:\n    test_a = None\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_bound_inside_try_block_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntry:\n    test_a = None\nexcept Exception: pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_bound_inside_while_block_is_dropped_not_removed():
    base = "def test_a(): assert 0\nwhile False:\n    test_a = None\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_test_rebound_by_walrus_is_dropped_not_removed():
    base = "def test_a(): assert 0\n_ = (test_a := 1)\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_an_owner_class_rebound_is_dropped_not_removed():
    base = "class A:\n    def test_a(self): pass\nA = None\n"
    side = _classify_single(base, "t.py::A::test_a")
    assert side.removed == set() and side.dropped == {"t.py::A::test_a"}


def test_a_plain_deleted_test_is_removed_control():
    base = "def test_a(): assert 0\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_a_module_level_global_in_a_branch_is_not_a_binding():
    """Review f844fc68 (:221): a module-level ``global`` binds nothing: delete is removed."""
    base = "if True:\n    global test_a\ndef test_a(): assert 0\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


@pytest.mark.parametrize(
    "binding",
    [
        "match value:\n    case [*test_a]: pass\n",
        "match value:\n    case {**test_a}: pass\n",
        "_ = [test_a := 1 for _ in range(1)]\n",
        "_ = (lambda x=(test_a := 1): x)\n",
    ],
    ids=["match-star", "match-mapping-rest", "comprehension-walrus", "lambda-default-walrus"],
)
@pytest.mark.parametrize("scope", ["module", "class"])
def test_a_test_rebound_by_pattern_or_nested_expression_is_dropped(scope, binding):
    if scope == "module":
        base = f"def test_a(): assert 0\n{binding}"
    else:
        indented_binding = "    " + binding.replace("\n", "\n    ").rstrip()
        base = f"class A:\n    def test_a(self): assert 0\n{indented_binding}\n"
    test_id = "t.py::test_a" if scope == "module" else "t.py::A::test_a"
    side = _classify_single(base, test_id)
    assert side.removed == set() and side.dropped == {test_id}


def test_comprehension_loop_variable_does_not_rebind_module_test():
    base = "def test_a(): assert 0\n_ = [test_a for test_a in values]\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_a_class_level_global_in_a_branch_still_refuses_control():
    """In a class body ``global`` sends the name to module scope: stay conservative."""
    base = "class TestC:\n    if True:\n        global test_a\n    def test_a(self): assert 0\n"
    side = _classify_single(base, "t.py::TestC::test_a")
    assert side.removed == set() and side.dropped == {"t.py::TestC::test_a"}


@pytest.mark.parametrize(
    "stmt",
    [
        "def f(x: (test_a := int)): pass\n",
        "def f() -> (test_a := int): pass\n",
        "def f(*, k: (test_a := int)): pass\n",
        "def f(*a: (test_a := int)): pass\n",
        "def f(**k: (test_a := int)): pass\n",
        "def f(p: (test_a := int), /): pass\n",
        "async def f(x: (test_a := int)): pass\n",
        "def f(x=(test_a := 1)): pass\n",
        "@(test_a := staticmethod)\ndef f(): pass\n",
    ],
    ids=[
        "arg-annotation-walrus",
        "return-annotation-walrus",
        "kwonly-annotation-walrus",
        "vararg-annotation-walrus",
        "kwarg-annotation-walrus",
        "posonly-annotation-walrus",
        "async-arg-annotation-walrus",
        "default-walrus",
        "decorator-walrus",
    ],
)
def test_a_test_rebound_by_function_annotation_walrus_is_dropped_not_removed(stmt):
    base = f"def test_a(): assert 0\n{stmt}"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_a_function_plain_annotation_without_walrus_does_not_rebind_test():
    base = "def test_a(): assert 0\ndef f(x: int) -> int: pass\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_a_class_rebound_by_another_class_header_walrus_is_dropped_not_removed():
    base = (
        "class TestX:\n    def test_a(self): assert 0\n"
        "class Other(metaclass=(TestX := type)): pass\n"
    )
    side = _classify_single(base, "t.py::TestX::test_a")
    assert side.removed == set() and side.dropped == {"t.py::TestX::test_a"}


def test_a_later_same_name_def_still_counts_as_defined():
    base = "def test_a(): assert 0\ndef test_a(): assert 1\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_a_class_header_walrus_does_not_borrow_the_other_class_s_same_name_test():
    # the owner check must compare names: Other also has a test_a, but it is not TestX's test_a
    base = (
        "class TestX:\n    def test_a(self): assert 0\n"
        "class Other(metaclass=(TestX := type)):\n    def test_a(self): assert 0\n"
    )
    side = _classify_single(base, "t.py::TestX::test_a")
    assert side.removed == set() and side.dropped == {"t.py::TestX::test_a"}


def test_a_base_annotation_walrus_does_not_widen_the_1127_base_excuse():
    # Opus review of #1135: the base's dead annotation walrus must not excuse the test commit's
    # hidden copy (main: dropped). Ids as pytest collects them on 3.11-3.13.
    base = (
        "class TestZ:\n    def test_a(self): assert 0\n"
        "class TestY:\n    def test_a(self): assert 0\n"
        "    if False:\n        def f(self, x: (test_a := int)): pass\n"
    )
    test = (
        "class TestZ:\n    pass\n"
        "class TestY:\n    def test_a(self): assert 0\n    def test_a(self): assert 1\n"
    )
    side = classify(
        {"t.py::TestZ::test_a", "t.py::TestY::test_a"},
        {"t.py::TestY::test_a"},
        {"t.py"},
        lambda p: test,
        lambda p: base,
    )
    assert "t.py::TestZ::test_a" in side.dropped and "t.py::TestZ::test_a" not in side.removed


def test_a_base_annotation_walrus_in_an_unchanged_helper_does_not_refuse_an_honest_deletion():
    # #1144: the test commit deletes test_a and keeps Helper unchanged. The base's annotation
    # walrus is an unchanged statement, so the base excuse must still see it: removed, not dropped.
    helper = "class Helper:\n    def f(self, x: (test_a := int)): pass\n"
    side = classify(
        {"t.py::test_a"},
        set(),
        {"t.py"},
        lambda p: helper,
        lambda p: "def test_a(): assert 0\n" + helper,
    )
    assert side.removed == {"t.py::test_a"} and side.dropped == set()
