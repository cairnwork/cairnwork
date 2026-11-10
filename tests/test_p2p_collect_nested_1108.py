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


def test_outer_class_test_is_dropped_when_nested_class_decorator_mentions_name():
    source = 'class A:\n    @add_test("test_x")\n    class I:\n        pass\n'
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_outer_class_test_is_dropped_when_nested_class_base_mentions_name():
    source = "class A:\n    class I(make_base(test_x)):\n        pass\n"
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_outer_class_test_is_dropped_when_nested_class_keyword_mentions_name():
    source = "class A:\n    class I(metaclass=M, tag=test_x):\n        pass\n"
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_nested_class_in_if_is_checked_by_name():
    side = _classify_a("class A:\n    if True:\n        class test_x: pass\n")
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_nested_class_in_try_is_checked_by_name():
    side = _classify_a(
        "class A:\n    try:\n        class test_x: pass\n    except Exception:\n        pass\n"
    )
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_nested_class_in_with_is_checked_by_name():
    side = _classify_a("class A:\n    with context():\n        class test_x: pass\n")
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_nested_class_type_params_are_checked_when_supported():
    import sys

    if sys.version_info < (3, 12):
        return
    side = _classify_a("class A:\n    class I[T: test_x]: pass\n")
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}


def test_nested_class_dynamic_namespace_body_is_walked():
    source = "class A:\n    class I:\n        sys._getframe(1).f_locals['aliased'] = test_x\n"
    side = _classify_a(source)
    assert side.removed == set() and side.dropped == {"t.py::A::test_x"}
