"""#1128: classify deleted inherited tests only with one provable same-file origin."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify


def _split(base: str, test: str, base_ids, test_ids):
    side = classify(
        set(base_ids),
        set(test_ids),
        {"t.py"},
        lambda p: test,
        lambda p: base,
        exposed=(),
        changed_python_files=["t.py"],
    )
    return side.removed, side.dropped


KEEP = "    def test_keep(self):\n        assert True\n"
X = "    def test_x(self):\n        assert True\n"


INHERITED = {
    "I1": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    pass\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\n",
        [
            "t.py::TestA::test_x",
            "t.py::TestA::test_keep",
            "t.py::TestB::test_x",
            "t.py::TestB::test_keep",
        ],
        ["t.py::TestA::test_keep", "t.py::TestB::test_keep"],
        {"t.py::TestA::test_x", "t.py::TestB::test_x"},
    ),
    "T5": (
        "class TestA:\n"
        + X
        + KEEP
        + "class TestB(TestA):\n    pass\nclass TestC(TestB):\n    pass\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\nclass TestC(TestB):\n    pass\n",
        [f"t.py::{c}::{n}" for c in ("TestA", "TestB", "TestC") for n in ("test_x", "test_keep")],
        [f"t.py::{c}::test_keep" for c in ("TestA", "TestB", "TestC")],
        {f"t.py::{c}::test_x" for c in ("TestA", "TestB", "TestC")},
    ),
    "T6": (
        "class Base:\n" + X + KEEP + "class TestB(Base):\n    pass\nclass TestC(Base):\n    pass\n",
        "class Base:\n" + KEEP + "class TestB(Base):\n    pass\nclass TestC(Base):\n    pass\n",
        [f"t.py::{c}::{n}" for c in ("TestB", "TestC") for n in ("test_x", "test_keep")],
        [f"t.py::{c}::test_keep" for c in ("TestB", "TestC")],
        {f"t.py::{c}::test_x" for c in ("TestB", "TestC")},
    ),
    "T7": (
        "class Base:\n" + X + KEEP + "class TestB(Base):\n    pass\n",
        "class Base:\n" + KEEP + "class TestB(Base):\n    pass\n",
        ["t.py::TestB::test_x", "t.py::TestB::test_keep"],
        ["t.py::TestB::test_keep"],
        {"t.py::TestB::test_x"},
    ),
}


@pytest.mark.parametrize("case", sorted(INHERITED))
def test_inherited_deletion_is_removed(case):
    base, test, base_ids, test_ids, expected = INHERITED[case]
    assert _split(base, test, base_ids, test_ids) == (expected, set())


# These ids are pytest --collect-only results from the #1296 counterexamples.
HIDING = {
    "hidden-class-copy": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Base: pass\nclass Hidden:\n    def test_x(self): pass\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "conditional-parent-method": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Base:\n    if False:\n        def test_x(self): pass\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "function-dunder-test": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "def _f(self): pass\n_f.__test__=False\nclass Base:\n    test_x=_f\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "parent-rebound-assign": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "def _f(self): pass\n_f.__test__=False\nclass Base: pass\nBase.test_x=_f\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "mixin-shadow": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Mix:\n    test_x=None\nclass Base:\n    def test_x(self): pass\n"
        "class TestB(Mix, Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "nested-inner-class": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Base:\n    class Inner:\n        def test_x(self): pass\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "dynamic-type-parent": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Base:\n    def test_x(self): pass\nBase = type('Base',(),{})\n"
        "class TestB(Base):\n    def test_k(self): pass\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        ["t.py::TestB::test_k"],
        {"t.py::TestB::test_x"},
    ),
    "owner-hidden-test-false": (
        "class Base:\n    def test_x(self): pass\nclass TestB(Base):\n    def test_k(self): pass\n",
        "class Base: pass\nclass TestB(Base):\n    __test__ = False\n",
        ["t.py::TestB::test_k", "t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_k", "t.py::TestB::test_x"},
    ),
}


@pytest.mark.parametrize("case", sorted(HIDING))
def test_hiding_shapes_stay_dropped(case):
    base, test, base_ids, test_ids, expected = HIDING[case]
    assert _split(base, test, base_ids, test_ids) == (set(), expected)


BLOCKED = {
    "ambiguous-ancestors": (
        "class A:\n" + X + "class B:\n" + X + "class TestC(A, B):\n    pass\n",
        "class A: pass\nclass B:\n" + X + "class TestC(A):\n    pass\n",
        ["t.py::TestC::test_x"],
        [],
        {"t.py::TestC::test_x"},
    ),
    "attribute-base": (
        "class mod:\n    class Base:\n        def test_x(self):\n            assert True\n"
        "class TestB(mod.Base):\n    pass\n",
        "class mod:\n    class Base: pass\nclass TestB(mod.Base):\n    pass\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
    "metaclass-base": (
        "class Meta(type): pass\nclass Base(metaclass=Meta):\n"
        + X
        + "class TestB(Base):\n    pass\n",
        "class Meta(type): pass\nclass Base(metaclass=Meta): pass\nclass TestB(Base):\n    pass\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
    "nested-owner": (
        "class Base:\n" + X + "class TestO:\n    class TestB(Base):\n        pass\n",
        "class Base: pass\nclass TestO:\n    class TestB(Base):\n        pass\n",
        ["t.py::TestO::TestB::test_x"],
        [],
        {"t.py::TestO::TestB::test_x"},
    ),
    "subclass-constant": (
        "class Base:\n" + X + "class TestB(Base):\n    pass\n",
        "class Base: pass\nclass TestB(Base):\n    test_x = None\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
    "subclass-false-branch": (
        "class Base:\n" + X + "class TestB(Base):\n    pass\n",
        "class Base: pass\nclass TestB(Base):\n    if False:\n        def test_x(self): pass\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
    "module-assignment": (
        "class Base:\n" + X + "class TestB(Base):\n    pass\n",
        "def _f(self): pass\n_f.__test__ = False\nclass Base: pass\n"
        "class TestB(Base):\n    pass\nTestB.test_x = _f\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
    "base-init-subclass": (
        "class Base:\n" + X + "class TestB(Base):\n    pass\n",
        "class Base:\n    def __init_subclass__(cls):\n        cls.test_x = None\n"
        "class TestB(Base):\n    pass\n",
        ["t.py::TestB::test_x"],
        [],
        {"t.py::TestB::test_x"},
    ),
}


@pytest.mark.parametrize("case", sorted(BLOCKED))
def test_unprovable_inherited_deletion_stays_dropped(case):
    base, test, base_ids, test_ids, expected = BLOCKED[case]
    assert _split(base, test, base_ids, test_ids) == (set(), expected)


def test_subclass_detached_from_a_kept_origin_stays_dropped():
    # the base test's code stays in Base; TestB only stops inheriting it (origin-scope check)
    base = "class Base:\n" + X + KEEP + "class TestB(Base):\n    pass\n"
    test = "class Base:\n" + X + KEEP + "class TestB:\n" + KEEP
    ids = ["t.py::TestB::test_x", "t.py::TestB::test_keep"]
    assert _split(base, test, ids, ids[1:]) == (set(), {"t.py::TestB::test_x"})


def test_origin_is_none_when_two_ancestors_define_the_name():
    import ast

    from cairnwork.p2p_classify_child import _inherited_origin

    two = "class A:\n" + X + "class B:\n" + X + "class TestC(A, B):\n" + KEEP
    assert _inherited_origin(ast.parse(two), ["TestC"], "test_x") is None
    one = "class A:\n" + X + "class B:\n" + KEEP + "class TestC(A, B):\n    pass\n"
    assert _inherited_origin(ast.parse(one), ["TestC"], "test_x") == "A"


MOVED = {
    # #1426 review: the inherited test's code moved to another collected class (Codex, Gemini)
    "moved-to-unrelated-class": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    pass\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\nclass TestC:\n" + X,
        [
            "t.py::TestA::test_x",
            "t.py::TestA::test_keep",
            "t.py::TestB::test_x",
            "t.py::TestB::test_keep",
        ],
        ["t.py::TestA::test_keep", "t.py::TestB::test_keep", "t.py::TestC::test_x"],
    ),
    "moved-to-sibling-subclass": (
        "class Base:\n" + X + KEEP + "class TestA(Base):\n    pass\nclass TestB(Base):\n    pass\n",
        "class Base:\n" + KEEP + "class TestA(Base):\n" + X + "class TestB(Base):\n    pass\n",
        [
            "t.py::TestA::test_x",
            "t.py::TestA::test_keep",
            "t.py::TestB::test_x",
            "t.py::TestB::test_keep",
        ],
        ["t.py::TestA::test_x", "t.py::TestA::test_keep", "t.py::TestB::test_keep"],
    ),
}


@pytest.mark.parametrize("case", sorted(MOVED))
def test_inherited_test_moved_to_another_collected_class_stays_dropped(case):
    base, test, base_ids, test_ids = MOVED[case]
    removed, dropped = _split(base, test, base_ids, test_ids)
    assert "t.py::TestB::test_x" in dropped
    assert "t.py::TestB::test_x" not in removed


B7 = "class Base:\n" + X + KEEP + "class TestB(Base):\n    pass\n"
B_IDS = ["t.py::TestB::test_x", "t.py::TestB::test_keep"]
T_IDS = ["t.py::TestB::test_keep"]
SHADOWED = {
    # #1426 Claude review: the origin's def kept byte-for-byte under a shadowed same-name class
    "C1-detach": (B7, "class Base:\n" + X + "class Base:\n" + KEEP + "class TestB:\n" + KEEP),
    "C5-reparent": (
        B7,
        "class Base:\n"
        + X
        + "class Base:\n"
        + KEEP
        + "class Clean:\n"
        + KEEP
        + "class TestB(Clean):\n    pass\n",
    ),
    "C6-shadow-after": (B7, "class Base:\n" + X + "class TestB:\n" + KEEP + "class Base:\n" + KEEP),
    "C8-mixin": (
        "class Mixin:\n" + X + "class TestB(Mixin):\n" + KEEP,
        "class Mixin:\n" + X + "class Mixin:\n    pass\nclass TestB:\n" + KEEP,
    ),
    "C11-three-levels": (
        "class Base:\n" + X + "class Mid(Base):\n    pass\nclass TestB(Mid):\n" + KEEP,
        "class Base:\n"
        + X
        + "class Base:\n    pass\nclass Mid(Base):\n    pass\nclass TestB:\n"
        + KEEP,
    ),
    "Me-base-side-init-subclass": (
        "class Base:\n"
        + X
        + KEEP
        + "    def __init_subclass__(cls, **kw):\n        super().__init_subclass__(**kw)\n"
        + "class TestB(Base):\n    pass\n",
        "class Base:\n" + KEEP + "class TestB(Base):\n    pass\n",
    ),
    "Mf-ancestor-mentions-without-def": (
        "def _g(self):\n    assert True\nclass Mid:\n    test_x = _g\nclass TestB(Mid):\n" + KEEP,
        "def _g(self):\n    assert True\nclass Mid:\n    pass\nclass TestB(Mid):\n" + KEEP,
    ),
}


@pytest.mark.parametrize("case", sorted(SHADOWED))
def test_shadowed_or_unprovable_origin_stays_dropped(case):
    base, test = SHADOWED[case]
    assert _split(base, test, B_IDS, T_IDS) == (set(), {"t.py::TestB::test_x"})


def test_two_detached_subclasses_with_a_shadowed_origin_stay_dropped():
    base = (
        "class Base:\n" + X + KEEP + "class TestB(Base):\n    pass\nclass TestC(Base):\n    pass\n"
    )
    test = (
        "class Base:\n"
        + X
        + "class Base:\n"
        + KEEP
        + "class TestB:\n"
        + KEEP
        + "class TestC:\n"
        + KEEP
    )
    gone = {"t.py::TestB::test_x", "t.py::TestC::test_x"}
    kept = ["t.py::TestB::test_keep", "t.py::TestC::test_keep"]
    assert _split(base, test, [*gone, *kept], kept) == (set(), gone)


@pytest.mark.parametrize(
    "base,test",
    [
        (
            "class Mixin:\n" + X + "class TestB(Mixin):\n" + KEEP,
            "class Mixin:\n    pass\nclass TestB(Mixin):\n" + KEEP,
        ),
        (B7, "class Base:\n" + KEEP + "class TestB:\n" + KEEP),
    ],
    ids=["P2-mixin", "P4-deleted-and-detached"],
)
def test_deleted_origin_without_a_kept_copy_is_removed(base, test):
    assert _split(base, test, B_IDS, T_IDS) == ({"t.py::TestB::test_x"}, set())
