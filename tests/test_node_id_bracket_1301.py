from types import SimpleNamespace

from cairnwork import p2p_conftest, p2p_nodes, pipeline_judge
from cairnwork.node_ids import unmatched_note


def test_unmatched_note_preserves_brackets_in_path_1301():
    gone = {"tests/dir[1]/test_x.py::test_a[old]"}
    assert unmatched_note(gone, {"tests/dir[1]/test_x.py::test_b[new]"}) == ""
    assert "per-run" in unmatched_note(gone, {"tests/dir[1]/test_x.py::test_a[new]"})


def test_p2p_nodes_passing_targets_preserves_brackets_in_path_1301():
    outcomes = {
        "tests/dir[1]/test_x.py::test_a[1]": "passed",
        "tests/dir[1]/test_x.py::test_b[1]": "passed",
    }
    targets, bound, left = p2p_nodes.passing_targets(
        outcomes,
        changed_files=frozenset({"tests/dir[1]/test_x.py"}),
        changed=frozenset({"tests/dir[1]/test_x.py::test_a"}),
    )
    assert targets == ("tests/dir[1]/test_x.py::test_b",)
    assert bound == ("tests/dir[1]/test_x.py::test_b[1]",)
    assert left == 1


def test_p2p_conftest_check_overlay_preserves_brackets_in_path_1301(monkeypatch):
    monkeypatch.setattr(p2p_conftest, "lay_tests_over", lambda *a: "c" * 40)
    monkeypatch.setattr(
        p2p_conftest,
        "_side",
        lambda *a: (
            {
                "tests/dir[1]/test_x.py::test_a[1]": "failed",
                "tests/dir[1]/test_x.py::test_b[1]": "passed",
            },
            "",
        ),
    )
    task = SimpleNamespace(repo=None, base_commit="", test_commit="")
    side = SimpleNamespace(
        changed=frozenset({"tests/dir[1]/test_x.py::test_a"}),
        probe_only=frozenset(),
    )
    result, ctx = SimpleNamespace(attempt_id="a"), SimpleNamespace(run_root="/nonexistent")
    p2p_conftest.check_overlay(
        task,
        ["tests/conftest.py"],
        (),
        {
            "tests/dir[1]/test_x.py::test_a[1]": "passed",
            "tests/dir[1]/test_x.py::test_b[1]": "passed",
        },
        side,
        result,
        ctx,
        1,
    )


def test_pipeline_judge_failed_targets_preserves_brackets_in_path_1301():
    run = SimpleNamespace(
        outcomes={
            "tests/dir[1]/test_x.py::test_a[1]": "failed",
            "tests/dir[2]/test_y.py::test_b[1]": "failed",
        }
    )
    targets = frozenset({"tests/dir[1]/test_x.py::test_a"})
    assert pipeline_judge._failed_targets([run], targets) == frozenset(
        {"tests/dir[1]/test_x.py::test_a[1]"}
    )
