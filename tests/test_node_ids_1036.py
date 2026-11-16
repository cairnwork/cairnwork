from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import p2p_conftest
from cairnwork.deferred_overlay import TestCommitRejected as _TestCommitRejected
from cairnwork.node_ids import mask_paths, normalise_run_ids, show, unmatched_note, without_case
from cairnwork.p2p_function import _function as function_from_p2p
from cairnwork.p2p_nodes import _function as function_from_nodes
from cairnwork.verdict_record import Verdict


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("dir[1]/test_a.py::test_x[case]", "dir[1]/test_a.py::test_x"),
        ("a::b/test_c.py::test_d", "a::b/test_c.py::test_d"),
        ("tests/test_x.py::TestClass::test_x[case]", "tests/test_x.py::TestClass::test_x"),
        ("tests/t.py::test_y[x::y]", "tests/t.py::test_y"),
    ],
)
def test_shared_node_id_parser_preserves_file_path_and_strips_case(nodeid, expected):
    assert without_case(nodeid) == expected
    assert function_from_p2p(nodeid) == expected
    assert function_from_nodes(nodeid) == expected


def test_case_ids_containing_double_colon_are_stripped_in_every_part():
    nodeid = "tests/t.py::C[a::b]::test_y[c::d]"
    assert without_case(nodeid) == "tests/t.py::C[a::b]::test_y"
    assert without_case(nodeid, all_parts=True) == "tests/t.py::C::test_y"
    assert function_from_p2p(nodeid) == "tests/t.py::C::test_y"
    assert function_from_nodes(nodeid) == "tests/t.py::C::test_y"


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


def test_placeholder_literal_is_escaped_without_conflating_checkout_root(tmp_path):
    root = tmp_path / "checkout"
    literal = Verdict(True, {f"t[{root}/a]": "passed", "t[<cw-checkout>/b]": "passed"})
    assert set(normalise_run_ids(literal, root).outcomes) == {
        "t[<cw-checkout>/a]",
        r"t[\<cw-checkout>/b]",
    }
    plain = Verdict(True, {r"t[a\b]": "passed", "t[x]": "passed"})
    assert normalise_run_ids(plain, root).outcomes == plain.outcomes


@pytest.mark.parametrize("reverse", [False, True])
def test_backslash_escape_preserves_bytes_id_distinction(tmp_path, reverse):
    root, other = tmp_path / "checkout", tmp_path / "other-run"
    path_id = f"t[\\{root}]"
    literal_id = "t[<cw-checkout>]"
    path_verdict = Verdict(True, {path_id: "passed"})
    literal_verdict = Verdict(True, {literal_id: "passed"})

    if reverse:
        path_verdict, literal_verdict = literal_verdict, path_verdict
        path_root, literal_root = other, root
    else:
        path_root, literal_root = root, other

    path_result = normalise_run_ids(path_verdict, path_root)
    literal_result = normalise_run_ids(literal_verdict, literal_root)
    assert set(path_result.outcomes) != set(literal_result.outcomes)
    if reverse:
        assert set(path_result.outcomes) == {r"t[\<cw-checkout>]"}
        assert set(literal_result.outcomes) == {r"t[\\<cw-checkout>]"}
    else:
        assert set(path_result.outcomes) == {r"t[\\<cw-checkout>]"}
        assert set(literal_result.outcomes) == {r"t[\<cw-checkout>]"}


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
    assert "if a parametrised test id differs" in unmatched_note(gone, {"t.py::test_x[new]"})


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
    assert show("test_cx[/x/y/z-1]").endswith("]")


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("dir.pyx/tests/t.py::test_x[param]", "dir.pyx/tests/t.py::test_x"),
        ("a.pyc/t.py::C::test_x[p]", "a.pyc/t.py::C::test_x"),
        ("dir.py/t.py::test_x[a.py::b]", "dir.py/t.py::test_x"),
        ("tests/doc.txt::test_x[foo.py::bar]", "tests/doc.txt::test_x"),
    ],
)
def test_py_inside_directory_name_is_not_the_file_boundary_1269(nodeid, expected):
    assert without_case(nodeid) == expected


@pytest.mark.parametrize("all_parts", [False, True])
def test_bare_names_lose_case_and_bare_file_paths_stay_whole_1269(all_parts):
    assert without_case("test_func[case]", all_parts=all_parts) == "test_func"
    assert without_case("tests/dir[1]/test_x.py", all_parts=all_parts) == "tests/dir[1]/test_x.py"


def test_unmatched_note_bare_names_and_unparameterised_variants_1269():
    assert "per-run" in unmatched_note({"test_x[a]"}, {"test_x[b]"})
    assert unmatched_note({"t.py::test_x[a]"}, {"t.py::test_x[a]"}) == ""
