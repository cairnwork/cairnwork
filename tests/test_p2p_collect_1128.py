"""Regression coverage for the two honest deletion shapes in Issue #1128."""

import pytest

from cairnwork.p2p_collect import classify


def split(base, test, base_ids, test_ids):
    side = classify(set(base_ids), set(test_ids), {"t.py"}, lambda _: test, lambda _: base)
    return side.removed, side.dropped


def _ids(*names):
    return [f"t.py::{name}" for name in names]


ANNOTATED_REBIND = {
    "with-value": "test_x: object = _other\n",
    "walrus-in-annotation": "test_x: (test_x := _other)\n",
    "plain-assign": "test_x = _other\n",
}


@pytest.mark.parametrize("case", sorted(ANNOTATED_REBIND))
def test_a_real_rebinding_next_to_an_annotation_is_still_dropped(case):
    other = "def test_x():\n    assert True\n"
    base = other + "def test_x():\n    assert True\n" + ANNOTATED_REBIND[case]
    base += "def test_keep():\n    assert True\n"
    test = other + "def test_keep():\n    assert True\n"
    assert split(base, test, _ids("test_x", "test_keep"), _ids("test_keep")) == (
        set(),
        {"t.py::test_x"},
    )


def test_deleted_same_file_base_test_removes_inherited_id():
    base = "class TestA:\n    def test_x(self): pass\nclass TestB(TestA): pass\n"
    test = "class TestA: pass\nclass TestB(TestA): pass\n"
    ids = {"t.py::TestA::test_x", "t.py::TestB::test_x"}
    assert split(base, test, ids, set()) == (ids, set())


def test_param_case_removed_while_function_remains_is_removed():
    base = "import pytest\n@pytest.mark.parametrize('n', [1, 2])\ndef test_x(n): pass\n"
    test = "import pytest\n@pytest.mark.parametrize('n', [1])\ndef test_x(n): pass\n"
    assert split(base, test, {"t.py::test_x[1]", "t.py::test_x[2]"}, {"t.py::test_x[1]"}) == (
        {"t.py::test_x[2]"},
        set(),
    )


def test_real_test_drop_remains_refused():
    base = "class TestA:\n    def test_x(self): pass\n    def test_keep(self): pass\n"
    test = (
        "class TestA:\n    if False:\n        def test_x(self): pass\n"
        "    def test_keep(self): pass\n"
    )
    assert split(
        base,
        test,
        {"t.py::TestA::test_x", "t.py::TestA::test_keep"},
        {"t.py::TestA::test_keep"},
    ) == (
        set(),
        {"t.py::TestA::test_x"},
    )


def test_subclass_override_that_is_deleted_remains_refused():
    base = "class TestA: pass\nclass TestB(TestA):\n    def test_x(self): pass\n"
    test = "class TestA: pass\nclass TestB(TestA):\n    if False:\n        def test_x(self): pass\n"
    assert split(base, test, {"t.py::TestB::test_x"}, set()) == (
        set(),
        {"t.py::TestB::test_x"},
    )


def test_param_case_removed_with_function_remains_refused():
    base = (
        "import pytest\n@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_x(n): pass\ndef test_keep(): pass\n"
    )
    test = (
        "import pytest\n@pytest.mark.parametrize('n', [1])\n"
        "def test_x(n): pass\ndef test_keep(): pass\n"
    )
    base_ids = {"t.py::test_x[1]", "t.py::test_x[2]", "t.py::test_keep"}
    test_ids = {"t.py::test_x[1]", "t.py::test_keep"}
    assert split(base, test, base_ids, test_ids) == ({"t.py::test_x[2]"}, set())


BLOCKED_INHERITANCE = {
    "hidden-class-copy": (
        "class Base: pass\nclass Hidden:\n    def test_x(self): pass\n"
        "class TestB(Base):\n    def test_k(self): pass\n"
    ),
    "conditional-parent-method": (
        "class Base:\n    if False:\n        def test_x(self): pass\n"
        "class TestB(Base):\n    def test_k(self): pass\n"
    ),
    "function-dunder-test": (
        "def _f(self): pass\n_f.__test__=False\nclass Base:\n    test_x=_f\n"
        "class TestB(Base):\n    def test_k(self): pass\n"
    ),
    "parent-rebound": (
        "def _f(self): pass\n_f.__test__=False\nclass Base: pass\nBase.test_x=_f\n"
        "class TestB(Base):\n    def test_k(self): pass\n"
    ),
    "mixin-shadow": (
        "class Mix:\n    test_x=None\nclass Base:\n    def test_x(self): pass\n"
        "class TestB(Mix, Base):\n    def test_k(self): pass\n"
    ),
    "nested-owner-method": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n"
        "    class Inner:\n        def test_x(self): pass\n    def test_k(self): pass\n"
    ),
    "dynamic-type-parent": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n"
        "    def test_k(self): pass\nBase = type('Base',(),{'test_x':lambda self: None})\n"
    ),
    "owner-hidden": "class Base: pass\nclass TestB(Base):\n    __test__ = False\n",
}


@pytest.mark.parametrize("case", sorted(BLOCKED_INHERITANCE))
def test_ambiguous_parent_deletion_stays_dropped(case):
    base = (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n"
    )
    test = BLOCKED_INHERITANCE[case]
    ids = {"t.py::TestB::test_x", "t.py::TestB::test_k"}
    assert split(base, test, ids, {"t.py::TestB::test_k"}) == (set(), {"t.py::TestB::test_x"})


def test_deleted_whole_parametrized_function_is_removed():
    base = (
        "import pytest\n@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_x(n): pass\ndef test_keep(): pass\n"
    )
    test = "def test_keep(): pass\n"
    ids = {"t.py::test_x[1]", "t.py::test_x[2]", "t.py::test_keep"}
    assert split(base, test, ids, {"t.py::test_keep"}) == (
        {"t.py::test_x[1]", "t.py::test_x[2]"},
        set(),
    )


def test_parent_method_delete_removes_inherited_ids_in_children():
    base = (
        "class TestA:\n    def test_x(self): pass\n"
        "class TestB(TestA):\n    def test_k(self): pass\n"
    )
    test = "class TestA: pass\nclass TestB(TestA):\n    def test_k(self): pass\n"
    ids = {"t.py::TestA::test_x", "t.py::TestB::test_x", "t.py::TestB::test_k"}
    assert split(base, test, ids, {"t.py::TestB::test_k"}) == (
        {"t.py::TestA::test_x", "t.py::TestB::test_x"},
        set(),
    )
