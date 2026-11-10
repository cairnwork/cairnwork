"""Regression coverage for nested classes in P2P removed-test classification."""

from cairnwork.p2p_collect import classify


def _classify_a(source: str):
    base = "class A:\n    def test_x(self): pass\n    class I:\n        def test_x(self): pass\n"
    return classify({"t.py::A::test_x"}, set(), {"t.py"}, lambda path: source, lambda path: base)


def test_outer_class_test_is_removed_when_only_nested_class_keeps_name():
    source = "class A:\n    class I:\n        def test_x(self): pass\n"
    side = _classify_a(source)
    assert side.removed == {"t.py::A::test_x"} and side.dropped == set()


def test_outer_class_test_is_dropped_when_rebound_from_nested_class():
    source = "class A:\n    class I:\n        def test_x(self): pass\n    test_x = I.test_x\n"
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_outer_class_test_is_dropped_when_nested_class_name_matches():
    source = "class A:\n    class test_x:\n        pass\n"
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}
