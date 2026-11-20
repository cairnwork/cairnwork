"""#1272: the file part of a node id is cut at the file boundary (``node_ids.locate``), not at the
first ``::``, so a test file path holding ``::`` survives (taskpaths, deferred_tests)."""

from types import SimpleNamespace

import pytest
from conftest import git

from cairnwork import node_ids
from cairnwork.deferred_tests import base_comparison, untargeted
from cairnwork.pipeline import TaskRejected
from cairnwork.reasons import COLLECT_HINT
from cairnwork.taskpaths import check_task_paths

ODD = "a::b/test_t.py"


@pytest.mark.parametrize(
    ("nodeid", "file"),
    [
        (f"{ODD}::test_y[1]", ODD),
        ("tests/t.py::TestC::test_y", "tests/t.py"),
        ("tests/t.py", "tests/t.py"),
        ("dir[1]/t.py::test_f[a]::b]", "dir[1]/t.py"),
        ("a::b::t.py::test_x[1]", "a::b::t.py"),  # several :: in the path
        ("t.py::test_x::", "t.py"),  # trailing ::
        ("t.py::::test_x", "t.py"),  # empty segment
        ("t.py::", "t.py"),  # no name at all
        ("::t.py::test_x[1]", "::t.py"),  # empty first path segment
        ("t.py::test_x[1", "t.py"),  # unclosed case
        ("t.py::tést_ü[ä]", "t.py"),  # non-ASCII identifier
        ("test_bare", "test_bare"),  # no :: at all: the whole id
    ],
)
def test_file_part_of_a_node_id(nodeid, file):
    assert node_ids.file_part(nodeid) == file


def test_untargeted_finds_the_file_of_a_path_with_double_colons():
    assert untargeted((ODD,), {f"{ODD}::test_y"}, (f"{ODD}::test_y",)) == ()
    assert untargeted((ODD,), {f"{ODD}::test_y"}, (f"{ODD}::test_other",)) == (ODD,)


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
    got = base_comparison([run], frozenset({f"{ODD}::test_y"}))
    assert COLLECT_HINT in got.detail
