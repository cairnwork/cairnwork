"""Issue #1222: bracket characters in paths do not corrupt node id parsing."""

import pytest

from cairnwork.p2p_collect import _function, classify


@pytest.mark.parametrize(
    ("nodeid", "base", "test", "expected"),
    [
        (
            "tests/a[b]/t.py::test_y",
            "def test_y():\n    pass\n",
            "",
            "removed",
        ),
    ],
)
def test_bracket_in_directory_still_classifies_removed_test(nodeid, base, test, expected):
    path = nodeid.partition("::")[0]
    side = classify({nodeid}, set(), {path}, lambda _: test, lambda _: base)
    assert getattr(side, expected) == {nodeid}


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [("tests/t.py::test_y[value]", "tests/t.py::test_y")],
)
def test_parametrize_suffix_is_removed_from_function_id(nodeid, expected):
    assert _function(nodeid) == expected


@pytest.mark.parametrize("nodeid", ["t.py"])
def test_malformed_node_id_is_dropped_without_raising(nodeid):
    side = classify({nodeid}, set(), {nodeid}, lambda _: "", lambda _: "def test_y(): pass\n")
    assert side.removed == set()
    assert side.dropped == {nodeid}
