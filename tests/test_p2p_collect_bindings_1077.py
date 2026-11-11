"""Issue #1077: ambiguous inheritance bindings stay dropped."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify


@pytest.mark.parametrize(
    "base",
    [
        "from unittest import TestCase\nTestCase = Other\nclass A(TestCase): pass\n",
        "from unittest import TestCase\nfrom other import TestCase\nclass A(TestCase): pass\n",
        "class Base: pass\nBase = Other\nclass A(Base): pass\n",
        "class Base: pass\nBase = inject(Base)\nclass A(Base): pass\n",
        "class Base: pass\nBase.__init_subclass__ = classmethod(f)\nclass A(Base): pass\n",
        "class Base:\n    __init_subclass__ = classmethod(f)\nclass A(Base): pass\n",
        "class Base:\n    if True:\n        def __init_subclass__(cls): pass\n"
        "class A(Base): pass\n",
    ],
    ids=[
        "single-name rebind",
        "multiple imports",
        "base rebind",
        "base decoration",
        "base attribute mutation",
        "class-body binding",
        "conditional class-body binding",
    ],
)
def test_ambiguous_base_evidence_stays_dropped(base: str) -> None:
    test_id = "t.py::A::test_x"
    base_file = "class A:\n    def test_x(self): pass\n"
    side = classify({test_id}, set(), {"t.py"}, lambda path: base, lambda path: base_file)
    assert side.removed == set()
    assert side.dropped == {test_id}
