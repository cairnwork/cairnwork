"""#1128 item 3 (annotation-only `x: T` is not a binding) is fixed, and only that exact shape: a
real rebinding next to an annotation stays refused (dropped). Items 1 and 2 stay refused as on main.
``classify`` gets the ids pytest collects for the two sources."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify


def _split(base, test, base_ids, test_ids):
    side = classify(set(base_ids), set(test_ids), {"t.py"}, lambda p: test, lambda p: base)
    return side.removed, side.dropped


def _ids(*names):
    return [f"t.py::{n}" for n in names]


ANNOTATED_REBIND = {
    "with-value": "test_x: object = _other\n",
    "walrus-in-annotation": "test_x: (test_x := _other)\n",
    "plain-assign": "test_x = _other\n",
}


@pytest.mark.parametrize("case", sorted(ANNOTATED_REBIND))
def test_a_real_rebinding_next_to_an_annotation_is_still_dropped(case):
    other = "def _other():\n    assert True\n"
    base = other + "def test_x():\n    assert True\n" + ANNOTATED_REBIND[case]
    base += "def test_keep():\n    assert True\n"
    test = other + "def test_keep():\n    assert True\n"
    assert _split(base, test, _ids("test_x", "test_keep"), _ids("test_keep")) == (
        set(),
        {"t.py::test_x"},
    )
