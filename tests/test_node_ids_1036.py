from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import p2p_conftest
from cairnwork.deferred_overlay import TestCommitRejected as _TestCommitRejected
from cairnwork.node_ids import mask_paths, normalise_run_ids, show, unmatched_note
from cairnwork.verdict_record import Verdict


def test_raw_and_realpath_checkout_spellings_normalise_only_parameter_segment(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    for spelling in (str(root), str(root.resolve())):
        verdict = Verdict(
            True,
            {f"tests/t.py::test_x[{spelling}/a]": "passed"},
            collected=(f"tests/t.py::test_x[{spelling}/a]",),
        )
        got = normalise_run_ids(verdict, root)
        assert tuple(got.outcomes) == ("tests/t.py::test_x[<cw-checkout>/a]",)
        assert got.collected == tuple(got.outcomes)


def test_root_boundary_and_other_roots_are_not_rewritten(tmp_path):
    root = tmp_path / "checkout"
    foreign = tmp_path / "foreign"
    ids = (f"t[{root}-evil/a]", f"t[{root}2/a]", f"t[x{root}/a]", f"t[{foreign}/a]")
    verdict = Verdict(True, {node: "passed" for node in ids})
    assert normalise_run_ids(verdict, root).outcomes == verdict.outcomes
    distinct = Verdict(True, {f"t[{root}/a]": "passed", f"t[{root}/b]": "passed"})
    assert set(normalise_run_ids(distinct, root).outcomes) == {
        "t[<cw-checkout>/a]",
        "t[<cw-checkout>/b]",
    }


def test_only_parameter_segment_and_only_this_runs_checkout_are_normalised(tmp_path):
    root, other = tmp_path / "checkout", tmp_path / "other-run"
    node = f"{root}::test_x[{root}/a]"
    verdict = Verdict(True, {node: "passed"})
    got = normalise_run_ids(verdict, root)
    assert tuple(got.outcomes) == (f"{root}::test_x[<cw-checkout>/a]",)
    foreign = Verdict(True, {f"t[{other}/a]": "passed"})
    assert normalise_run_ids(foreign, root).outcomes == foreign.outcomes


def test_placeholder_literal_and_colliding_ids_fail_closed(tmp_path):
    root = tmp_path / "checkout"
    literal = Verdict(True, {f"t[{root}/a]": "passed", "t[<cw-checkout>/b]": "passed"})
    assert normalise_run_ids(literal, root) is literal
    collision = Verdict(True, {f"t[{root}/a]": "passed", "t[<cw-checkout>/a]": "failed"})
    assert normalise_run_ids(collision, root) is collision


def test_raw_and_realpath_alias_collision_fails_closed(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    verdict = Verdict(
        True,
        {f"t[{alias}/a]": "passed", f"t[{root.resolve()}/a]": "failed"},
    )
    assert normalise_run_ids(verdict, alias) is verdict


def test_unmatched_note_requires_same_function_and_different_params():
    gone = {"t.py::test_x[old]"}
    assert "per-run" in unmatched_note(gone, {"t.py::test_x[new]"})
    assert unmatched_note(gone, {"t.py::test_y[new]"}) == ""
    assert unmatched_note(gone, {"t.py::test_x[old]"}) == ""


def test_conftest_overlay_uuid_ids_refuse_with_per_run_advice(monkeypatch):
    monkeypatch.setattr(p2p_conftest, "lay_tests_over", lambda *a: "c" * 40)
    monkeypatch.setattr(
        p2p_conftest,
        "_side",
        lambda *a: ({"tests/test_x.py::test_x[second-uuid]": "passed"}, ""),
    )
    task = SimpleNamespace(repo=None, base_commit="", test_commit="")
    side = SimpleNamespace(changed=set(), probe_only=set())
    result, ctx = SimpleNamespace(attempt_id="a"), SimpleNamespace(run_root="/nonexistent")

    with pytest.raises(_TestCommitRejected) as raised:
        p2p_conftest.check_overlay(
            task,
            ["tests/conftest.py"],
            (),
            {"tests/test_x.py::test_x[first-uuid]": "passed"},
            side,
            result,
            ctx,
            1,
        )

    assert raised.value.code == p2p_conftest.CHANGES_BASE
    assert "per-run" in str(raised.value) and "ids=" in str(raised.value)


def test_show_masks_paths_controls_and_bounds_text():
    root = str(Path("/Users/person/project").resolve())
    shown = show(f"test_x[{root}/src] \x1b" + "x" * 300)
    assert root not in shown and "<path>" in shown
    assert "\x1b" not in shown and len(shown) <= 200
    assert mask_paths("bad /private/tmp/run/state and /var/folders/a/b/file") == (
        "bad <path> and <path>"
    )
