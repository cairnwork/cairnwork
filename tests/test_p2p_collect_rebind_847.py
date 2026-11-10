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
