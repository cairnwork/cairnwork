"""Pin additional rebinding shapes in the P2P collection classifier (#1234)."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify, under_roots

TEST_ID = "t.py::test_a"


def _module_case(rebind: str):
    helper = "def helper():\n    assert 0\n"
    base = helper + "def test_a():\n    helper()\n" + rebind
    test = helper
    side = classify({TEST_ID}, set(), {"t.py"}, lambda p: test, lambda p: base)
    return side


MODULE_SHAPES = {
    "for-iter": "for _ in [(test_a := helper)]:\n    pass\n",
    "while-test": "while (test_a := helper) is None:\n    pass\n",
    "match-subject": "match (test_a := helper):\n    case _:\n        pass\n",
    "match-guard": "match 1:\n    case _ if (test_a := helper):\n        pass\n",
    "dict-update": "globals().update({'test_a': helper})\n",
    "module-setattr": ("import sys\nsys.modules[__name__].__setattr__('test_a', helper)\n"),
    "wrapped-update": "def update(**k):\n    globals().update(k)\nupdate(test_a=helper)\n",
}


@pytest.mark.parametrize("shape", MODULE_SHAPES)
def test_module_rebinding_shapes_are_dropped(shape):
    side = _module_case(MODULE_SHAPES[shape])
    assert side.dropped == {TEST_ID}
    assert side.removed == set()


CLASS_SHAPES = {
    "dynamic-module-namespace": (
        "class TestX:\n"
        "    class Inner:\n"
        "        getattr(__import__('sys'), 'modules')[__name__].test_a = helper\n",
        "t.py::TestX::test_a",
    ),
    "nested-module-setattr": (
        "import sys\nclass TestX:\n"
        "    class Inner:\n"
        "        setattr(sys.modules[__name__], 'test_a', helper)\n",
        "t.py::TestX::test_a",
    ),
    "short-mark-decorator": (
        "import pytest\npytest.mark = lambda c: c\n"
        "@pytest.mark\nclass TestX:\n    def test_a(self):\n        helper()\n",
        "t.py::TestX::test_a",
    ),
    "deep-mark-decorator": (
        "from types import SimpleNamespace\n"
        "pytest.foo = SimpleNamespace(bar=lambda c: c)\n"
        "@pytest.foo.bar\nclass TestX:\n    def test_a(self):\n        helper()\n",
        "t.py::TestX::test_a",
    ),
}


@pytest.mark.parametrize("shape", CLASS_SHAPES)
def test_nested_class_rebinding_shapes_are_dropped(shape):
    test, nodeid = CLASS_SHAPES[shape]
    base = "def helper():\n    assert 0\nclass TestX:\n    def test_a(self):\n        helper()\n"
    side = classify({nodeid}, set(), {"t.py"}, lambda p: test, lambda p: base)
    assert side.dropped == {nodeid}
    assert side.removed == set()


def test_same_name_extra_copy_is_not_excused():
    base = (
        "class TestX:\n    def test_a(self):\n        assert 0\n"
        "    def test_b(self):\n        pass\n"
        "class _K:\n    def test_a(self):\n        assert 0\n"
    )
    test = (
        "class TestX:\n    def test_b(self):\n        pass\n"
        "class _K:\n    def test_a(self):\n        assert 0\n"
        "    def test_a(self):\n        assert 0\n"
    )
    side = classify(
        {"t.py::TestX::test_a", "t.py::TestX::test_b"},
        {"t.py::TestX::test_b"},
        {"t.py"},
        lambda p: test,
        lambda p: base,
    )
    assert side.dropped == {"t.py::TestX::test_a"}
    assert side.removed == set()


def test_ambiguous_testcase_alias_does_not_excuse_inherited_test():
    base = (
        "class MyBase:\n    __test__ = False\n"
        "    def test_a(self):\n        assert 0\n"
        "class TestX:\n    def test_a(self):\n        assert 0\n"
        "    def test_b(self):\n        pass\n"
    )
    test = (
        "class MyBase:\n    __test__ = False\n"
        "    def test_a(self):\n        assert 0\n"
        "unittest.Foo = MyBase\nfrom unittest import Foo as TestCase\n"
        "class TestX(TestCase):\n    def test_b(self):\n        pass\n"
    )
    ids = {"t.py::TestX::test_a", "t.py::TestX::test_b"}
    side = classify(ids, set(), {"t.py"}, lambda p: test, lambda p: base)
    assert side.dropped == ids
    assert side.removed == set()


def test_under_roots_matches_casefolded_path():
    paths = b"Tests/test_x.py\0"
    assert under_roots(paths, ("tests",)) == ["Tests/test_x.py"]
