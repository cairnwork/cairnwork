"""#1369: one case-id parser for every consumer; #1272: the file part of a node id is cut at the
file boundary (``node_ids.locate``), not at the first ``::``, so a path holding ``::`` survives."""

from types import SimpleNamespace

import pytest
from conftest import git

from cairnwork import node_ids, p2p_function
from cairnwork.deferred_tests import base_comparison, not_run, untargeted
from cairnwork.pipeline import TaskRejected
from cairnwork.taskpaths import check_task_paths

ODD = "a::b/test_t.py"
CASE_IDS = [
    "t.py::C[a]::test_y[c]",
    "tests/t.py::C[a::b]::test_y[c::d]",
    "t.py::a[b]]",
    "t.py::test_x::",
    "t.py::::test_x",
    "t.py::",
    "t.py::test_x[",
    "t.py::test_x[1",
    "t.py::tést_ü[ä]::b]",
    "a::b::t.py::test_x[1]",
    "::t.py::test_x[1]",
    "t.py::test_x[a]::b]",
    "t.py::test_p[a]::b[c]]",
    "t.py::test_p[]::t]",
    "t.py::TestT::test_m[k]::j]",
    "dir[1]/t.py::test_f[a]::b]",
    f"{ODD}::test_g[p]",
    "t.py::test_plain",
    "t.py",
    "test_bare",
]


AMBIGUOUS = {  # Name[x]::Name[y]: a parametrized class or one case id holding "]::" (see docstring)
    "t.py::C[a]::test_y[c]",
    "tests/t.py::C[a::b]::test_y[c::d]",
}


@pytest.mark.parametrize("nodeid", [n for n in CASE_IDS if n not in AMBIGUOUS])
def test_p2p_function_agrees_with_node_ids(nodeid):
    for all_parts in (False, True):
        assert p2p_function._without_case(nodeid, all_parts=all_parts) == node_ids.without_case(
            nodeid, all_parts=all_parts
        )


def test_the_two_entry_points_differ_only_on_the_ambiguous_shape():
    # #1276 pins the per-part reading (a parametrized class keeps its method), #1309 pins the
    # first-"[" cut (test_f[e]::f[g] -> test_f); one text cannot satisfy both, so both are named
    nodeid = "t.py::C[a]::test_y[c]"
    assert node_ids.without_case(nodeid) == "t.py::C"
    assert p2p_function._without_case(nodeid) == "t.py::C[a]::test_y"
    assert node_ids.without_case_by_part(nodeid, all_parts=True) == "t.py::C::test_y"
    assert node_ids.without_case("t.py::test_f[e]::f[g]") == "t.py::test_f"


def test_a_run_target_whose_case_id_holds_a_close_bracket_and_colons_is_not_reported_not_run():
    run = SimpleNamespace(outcomes={"t.py::test_x[a]::b]": "passed"})
    assert not_run([run], {"t.py::test_x"}) == []


def test_untargeted_keeps_a_target_whose_case_id_holds_a_close_bracket():
    nodes = {"t.py::test_p[a]::b[c]]"}
    assert untargeted(("t.py",), nodes, ("t.py::test_p",)) == ()


def test_untargeted_finds_the_file_of_a_path_with_double_colons():
    assert untargeted((ODD,), {f"{ODD}::test_y"}, (f"{ODD}::test_y",)) == ()
    assert untargeted((ODD,), {f"{ODD}::test_y"}, (f"{ODD}::test_other",)) == (ODD,)


def test_file_part_of_a_node_id():
    assert node_ids.file_part(f"{ODD}::test_y[1]") == ODD
    assert node_ids.file_part("tests/t.py::TestC::test_y") == "tests/t.py"
    assert node_ids.file_part("tests/t.py") == "tests/t.py"
    assert node_ids.file_part("dir[1]/t.py::test_f[a]::b]") == "dir[1]/t.py"


def test_a_verifier_target_in_a_path_with_double_colons_is_found_on_the_base(repo):
    (repo / "a::b").mkdir()
    (repo / "a::b" / "test_t.py").write_text("def test_y():\n    pass\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "odd path")
    base = git(repo, "rev-parse", "HEAD")
    check_task_paths(repo, base, ("src/a.py",), (f"{ODD}::test_y",))
    with pytest.raises(TaskRejected):
        check_task_paths(repo, base, ("src/a.py",), ("a::b/test_missing.py::test_y",))


def test_the_collect_hint_names_a_collection_error_in_a_path_with_double_colons():
    run = SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), returncode=2, outcomes={}, timed_out=False,
        report_too_big=False, recorded=True, collect_errors=(ODD,), mutated=(),
        teardown_errors=(), fail_kind={}, survivors=0, forged=(), log_too_big=False,
    )  # fmt: skip
    from cairnwork.reasons import COLLECT_HINT

    got = base_comparison([run], frozenset({f"{ODD}::test_y"}))
    assert COLLECT_HINT in got.detail
