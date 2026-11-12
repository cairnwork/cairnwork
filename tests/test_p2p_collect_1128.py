"""#1128: the three false refusals are fixed, and only the exact honest shapes: every neighbour that
hides or keeps the base test's code stays refused (dropped). ``classify`` gets the ids pytest
collects for the two sources, as in test_p2p_hidden_base_test_1127.py."""

from __future__ import annotations

import pytest

from cairnwork.p2p_collect import classify

KEEP = "    def test_keep(self):\n        assert True\n"
X = "    def test_x(self):\n        assert True\n"
P = "import pytest\n@pytest.mark.parametrize('n', [1, 2])\n"


def _split(base, test, base_ids, test_ids):
    side = classify(set(base_ids), set(test_ids), {"t.py"}, lambda p: test, lambda p: base)
    return side.removed, side.dropped


def _ids(*names):
    return [f"t.py::{n}" for n in names]


A_ALL = ["TestA::test_x", "TestA::test_keep", "TestB::test_x", "TestB::test_keep"]
# (base, test, base ids, test ids): the parent test is deleted; what the subclass inherits goes too
INHERIT_REFUSED = {
    # the test commit still names test_x in the parent: not a deletion
    "parent-still-mentions-name": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    pass\n",
        "class TestA:\n" + KEEP + "    x = 'test_x'\nclass TestB(TestA):\n    pass\n",
    ),
    # a second base can supply the name: the chain is not a single one
    "two-bases": (
        "class M:\n    pass\nclass TestA:\n" + X + KEEP + "class TestB(TestA, M):\n    pass\n",
        "class M:\n    pass\nclass TestA:\n" + KEEP + "class TestB(TestA, M):\n    pass\n",
    ),
    # the parent is imported: the file never defined the name for it
    "imported-parent": (
        "from other import TestA\nclass TestB(TestA):\n    pass\n",
        "from other import TestA\nclass TestB(TestA):\n    x = 1\n",
    ),
    # a metaclass / hook on the subclass can add tests the source never names
    "hook-on-subclass": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    def __init_subclass__(cls):\n"
        "        pass\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    def __init_subclass__(cls):\n"
        "        pass\n",
    ),
    # the parent is rebound after the subclass is built, so the chain is not the one defined
    "parent-bound-twice": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    pass\nTestA = object\n",
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\nTestA = object\n",
    ),
}


@pytest.mark.parametrize("case", sorted(INHERIT_REFUSED))
def test_inherited_test_is_refused_when_the_chain_is_not_one_plain_same_file_base(case):
    base, test = INHERIT_REFUSED[case]
    ids = _ids(*A_ALL)
    removed, dropped = _split(base, test, ids, _ids("TestA::test_keep", "TestB::test_keep"))
    assert "t.py::TestB::test_x" in dropped and "t.py::TestB::test_x" not in removed


def test_a_grandchild_inherits_through_a_chain_of_same_file_bases():
    base = (
        "class TestA:\n"
        + X
        + KEEP
        + "class TestB(TestA):\n    pass\nclass TestC(TestB):\n    pass\n"
    )
    test = (
        "class TestA:\n" + KEEP + "class TestB(TestA):\n    pass\nclass TestC(TestB):\n    pass\n"
    )
    ids = ["TestA::test_x", "TestA::test_keep", "TestB::test_x", "TestB::test_keep"]
    ids += ["TestC::test_x", "TestC::test_keep"]
    kept = ["TestA::test_keep", "TestB::test_keep", "TestC::test_keep"]
    removed, dropped = _split(base, test, _ids(*ids), _ids(*kept))
    assert dropped == set() and len(removed) == 3


PARAM_REFUSED = {
    # the function def is the inherited one: the base file never defined it itself
    "function-not-defined-in-base": (
        "from other import test_x\n",
        "from other import test_x\n",
    ),
    # a later class of the same name shadows the parametrized original: hidden, not removed
    "case-hidden-by-shadowing-class": (
        "import pytest\nclass TestA:\n    @pytest.mark.parametrize('n', [1, 2])\n"
        "    def test_x(self, n):\n        assert n\n",
        "import pytest\nclass TestA:\n    @pytest.mark.parametrize('n', [1, 2])\n"
        "    def test_x(self, n):\n        assert n\nclass TestA:\n"
        "    @pytest.mark.parametrize('n', [1])\n    def test_x(self, n):\n        assert n\n",
    ),
}


@pytest.mark.parametrize("case", sorted(PARAM_REFUSED))
def test_one_parametrized_case_is_refused_when_the_function_is_not_the_base_one(case):
    base, test = PARAM_REFUSED[case]
    ids = (
        ("test_x[1]", "test_x[2]") if "A" not in case else ("TestA::test_x[1]", "TestA::test_x[2]")
    )
    removed, dropped = _split(base, test, _ids(*ids), _ids(ids[0]))
    assert _ids(ids[1])[0] in dropped and not removed


def test_an_unparametrized_test_that_became_parametrized_is_still_dropped():
    base, test = "def test_x():\n    assert True\n", P + "def test_x(n):\n    assert n\n"
    assert _split(base, test, _ids("test_x"), _ids("test_x[1]", "test_x[2]")) == (
        set(),
        {"t.py::test_x"},
    )


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


PD = "import pytest\n"
PX = "@pytest.mark.parametrize('n', {})\ndef test_p(n):\n    assert n\n"
# round-1 review of #1214 (must block): the case vanished but the source does not show a deletion
CASE_KEPT = {
    "identical-source": (PD + PX.format("[1, 2]"), PD + PX.format("[1, 2]")),
    "identical-plus-generate-tests-hook": (
        PD + PX.format("[1, 2]"),
        PD + PX.format("[1, 2]") + "def pytest_generate_tests(metafunc):\n    pass\n",
    ),
    "value-still-in-source-sliced": (PD + PX.format("[1, 2]"), PD + PX.format("[1, 2][:1]")),
    "value-still-in-source-filtered": (
        PD + PX.format("[1, 2]"),
        PD + PX.format("[n for n in (1, 2) if n < 2]"),
    ),
}


@pytest.mark.parametrize("case", sorted(CASE_KEPT))
def test_a_vanished_case_without_a_deletion_in_the_source_is_dropped(case):
    base, test = CASE_KEPT[case]
    assert _split(base, test, _ids("test_p[1]", "test_p[2]"), _ids("test_p[1]")) == (
        set(),
        {"t.py::test_p[2]"},
    )


@pytest.mark.parametrize("kept", ["[1]", "[pytest.param(1)]"])
def test_a_deleted_case_is_removed(kept):
    removed, dropped = _split(
        PD + PX.format("[1, 2]"),
        PD + PX.format(kept),
        _ids("test_p[1]", "test_p[2]"),
        _ids("test_p[1]"),
    )
    assert removed == {"t.py::test_p[2]"} and not dropped


def test_a_path_with_a_bracket_never_crashes_and_stays_refused():
    # review of #1214: "[" in the path made one_case true and the id split raise ValueError
    base = "def test_y():\n    assert True\ndef test_z():\n    assert True\n"
    test = "def test_z():\n    assert True\n"
    ids = ["tests/a[b]/t.py::test_y", "tests/a[b]/t.py::test_z"]
    side = classify(set(ids), {ids[1]}, {"tests/a[b]/t.py"}, lambda p: test, lambda p: base)
    assert side.removed | side.dropped == {ids[0]}


TB = "class TestA:\n" + X + KEEP
GONE = "class TestA:\n" + KEEP
INHERIT_CHANGED = {
    "un-inherited": (TB + "class TestB(TestA):\n    pass\n", TB + "class TestB:\n    pass\n"),
    "base-swapped-to-same-file-class": (
        "class C:\n    pass\n" + TB + "class TestB(TestA):\n    pass\n",
        "class C:\n    pass\n" + TB + "class TestB(C):\n    pass\n",
    ),
    "base-swapped-to-imported-class": (
        TB + "class TestB(TestA):\n    pass\n",
        "from o import C\n" + TB + "class TestB(C):\n    pass\n",
    ),
    "nested-owner": (
        TB + "class TestOuter:\n    class TestA:\n" + X + "    class TestB(TestA):\n        pass\n",
        TB + "class TestOuter:\n    class TestA:\n" + X + "    class TestB:\n        pass\n",
    ),
}


@pytest.mark.parametrize("case", sorted(INHERIT_CHANGED))
def test_a_subclass_that_stopped_inheriting_is_dropped(case):
    base, test = INHERIT_CHANGED[case]
    ids = ["TestB::test_x"] if case != "nested-owner" else ["TestOuter::TestB::test_x"]
    removed, dropped = _split(base, test, _ids(*ids), [])
    assert not removed and dropped == set(_ids(*ids))


# base-side guards alone (the test side drops the rebinding / decorator, so only the base guard
# stands between these and "removed")
BASE_GUARD = {
    "parent-rebound-in-base": (
        "class TestA:\n" + X + KEEP + "class TestB(TestA):\n    pass\nTestA = object\n",
        GONE + "class TestB(TestA):\n    pass\n",
    ),
    "parent-decorated-in-base": (
        "def dec(c):\n    return c\n@dec\nclass TestA:\n"
        + X
        + KEEP
        + "class TestB(TestA):\n    pass\n",
        "def dec(c):\n    return c\n" + GONE + "class TestB(TestA):\n    pass\n",
    ),
}


@pytest.mark.parametrize("case", sorted(BASE_GUARD))
def test_the_base_side_guards_of_the_inherited_chain_hold_alone(case):
    base, test = BASE_GUARD[case]
    removed, dropped = _split(base, test, _ids("TestB::test_x"), [])
    assert not removed and dropped == {"t.py::TestB::test_x"}
