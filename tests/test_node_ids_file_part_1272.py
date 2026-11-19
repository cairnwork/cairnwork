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


@pytest.mark.parametrize("nodeid", CASE_IDS)
def test_p2p_function_agrees_with_node_ids_on_ids_without_a_parametrized_class(nodeid):
    want = node_ids.without_case(nodeid)
    assert p2p_function._without_case(nodeid) == want
    assert p2p_function._without_case(nodeid, all_parts=True) == want


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


def test_a_parametrized_class_keeps_its_method_name():
    # the old expectation of test_node_ids_1036 stays: only node_ids.without_case cuts at the
    # first "[", so dropping the method of a parametrized class is not a feature of p2p_function
    nodeid = "tests/t.py::C[a::b]::test_y[c::d]"
    assert p2p_function._without_case(nodeid) == "tests/t.py::C[a::b]::test_y"
    assert p2p_function._function(nodeid) == "tests/t.py::C::test_y"
    assert p2p_function._without_case("t.py::C[a]::test_z") == "t.py::C[a]::test_z"  # no case


@pytest.mark.parametrize(
    ("nodeid", "without", "file"),
    [
        ("a::b::t.py::test_x[1]", "a::b::t.py::test_x", "a::b::t.py"),  # several :: in the path
        ("t.py::test_x::", "t.py::test_x::", "t.py"),  # trailing :: is not a name: left whole
        ("t.py::::test_x", "t.py::::test_x", "t.py"),  # empty segment: left whole
        ("t.py::", "t.py::", "t.py"),  # no name at all
        ("::t.py::test_x[1]", "::t.py::test_x", "::t.py"),  # empty first path segment
        ("t.py::test_x[1", "t.py::test_x", "t.py"),  # unclosed case
    ],
)
def test_boundary_inputs_are_stable(nodeid, without, file):
    assert p2p_function._without_case(nodeid) == without
    assert node_ids.file_part(nodeid) == file
