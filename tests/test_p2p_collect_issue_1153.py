"""Tests for Issue #1153: resolve module-level compound statements for P2P test definitions."""

from __future__ import annotations

from cairnwork.p2p_collect import classify


def _classify_single(base: str, test_id: str = "t.py::test_a"):
    return classify({test_id}, set(), {"t.py"}, lambda p: "x = 1\n", lambda p: base)


# --- Allowed (fail on base, pass on head) ---


def test_def_in_module_if_is_resolved_like_pytest():
    base = "def test_a(): assert 0\nif True:\n    def test_a(): assert 1\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


# --- Still Refused (pass on both base and head) ---


def test_def_in_module_try_both_branches_is_dropped():
    base = "try:\n    def test_a(): assert 0\nexcept Exception:\n    def test_a(): assert 1\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_module_with_is_dropped():
    base = "with open('f'):\n    def test_a(): assert 0\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_while_false_is_dropped():
    base = "while False:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_for_empty_is_dropped():
    base = "for _ in []:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_try_except_handler_is_dropped():
    base = "try:\n    pass\nexcept Exception:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_try_else_is_dropped():
    base = "try:\n    pass\nexcept Exception:\n    pass\nelse:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_match_case_is_dropped():
    base = "match 1:\n    case 2:\n        def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_with_suppress_is_dropped():
    base = "from contextlib import suppress\nwith suppress(Exception):\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_star_import_after_def_is_dropped_not_removed():
    base = "def test_a(): assert 0\nfrom helper import *\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_star_import_in_module_if_is_dropped_not_removed():
    base = "import pytest\ndef test_a(): pass\nif True:\n    from helper import *\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_star_import_in_module_try_is_dropped_not_removed():
    base = (
        "import pytest\ndef test_a(): pass\ntry:\n    from helper import *\n"
        "except Exception:\n    pass\n"
    )
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_star_import_in_module_with_is_dropped_not_removed():
    base = "import pytest\ndef test_a(): pass\nwith open(__file__):\n    from helper import *\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_except_alias_rebind_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntry:\n    pass\nexcept Exception as test_a:\n    pass\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_comprehension_walrus_is_dropped_not_removed():
    base = "def test_a(): assert 0\n[ (test_a := f) for f in [1] ]\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_class_body_locals_write_is_dropped_not_removed():
    base = "class TestClass:\n    def test_a(self): assert 0\n    locals()['test_a'] = 1\n"
    side = _classify_single(base, "t.py::TestClass::test_a")
    assert side.removed == set() and side.dropped == {"t.py::TestClass::test_a"}


# --- Pinned (#1127, #1122, #847) ---


def test_pinned_1127_hidden_class_is_dropped_not_removed():
    base = "class TestA:\n    def test_x(self): assert True\n    def test_keep(self): assert True\n"
    test = base + "class TestA:\n    def test_keep(self): assert True\n"
    side = classify(
        {"t.py::TestA::test_x", "t.py::TestA::test_keep"},
        {"t.py::TestA::test_keep"},
        {"t.py"},
        lambda p: test,
        lambda p: base,
    )
    assert "t.py::TestA::test_x" in side.dropped and "t.py::TestA::test_x" not in side.removed


def test_pinned_1122_class_header_walrus_is_dropped_not_removed():
    base = (
        "class TestX:\n    def test_a(self): assert 0\n"
        "class Other(metaclass=(TestX := type)): pass\n"
    )
    side = _classify_single(base, "t.py::TestX::test_a")
    assert side.removed == set() and side.dropped == {"t.py::TestX::test_a"}


def test_pinned_847_lambda_rebind_is_dropped_not_removed():
    base = "def test_a(): assert 0\ntest_a = (lambda: None)\n"
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_except_as_test_name_is_a_rebind_in_a_maybe_run_block():
    # Only a top-level def or one in a surely-run if branch counts (#1153); `except E as name`
    # in a handler that may run rebinds the name, so the base cannot be trusted to supply it
    # (pinned on main by tests/test_p2p_rebind_mutants_1154.py, #1168).
    base = (
        "import pytest\ndef test_a(): pass\ntry:\n    pass\n"
        "except ValueError as test_a:\n    pass\n"
    )
    side = _classify_single(base)
    assert side.removed == set() and side.dropped == {"t.py::test_a"}


def test_deep_if_elif_chain_does_not_raise_recursion_error():
    branches = "if False:\n    def test_a(): pass\n" + "".join(
        "elif False:\n    pass\n" for _ in range(1500)
    )
    base = f"def test_a(): pass\n{branches}"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_constant_false_if_is_dropped():
    base = "if False:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()


def test_def_in_constant_true_if_with_else_assign_is_removed():
    base = "if True:\n    def test_a(): pass\nelse:\n    test_a = 1\n"
    side = _classify_single(base)
    assert side.removed == {"t.py::test_a"} and side.dropped == set()


def test_def_in_non_constant_if_without_else_is_dropped():
    base = "if cond:\n    def test_a(): pass\n"
    side = _classify_single(base)
    assert side.dropped == {"t.py::test_a"} and side.removed == set()
