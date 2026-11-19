"""Node id parsing: results must equal what origin/main gave on every real pytest id."""

from types import SimpleNamespace

import pytest

from cairnwork import p2p_nodes, pipeline_judge
from cairnwork.deferred_tests import untargeted
from cairnwork.node_ids import unmatched_note, without_case

T = "tests/test_x.py"


@pytest.mark.parametrize("target", [f"{T}[x]", "tests/test_x.pyc[x]"])
def test_a_case_on_the_file_itself_names_nothing(target):
    assert untargeted((T,), {f"{T}::test_y"}, (target,)) == (T,)


@pytest.mark.parametrize(
    ("failed", "target"),
    [
        ("tests/test_t.py::test_f[a]::b]", "tests/test_t.py::test_f"),
        ("tests/dir[1]/test_t.py::test_f[a]::b]", "tests/dir[1]/test_t.py::test_f"),
        ("q]/test_t.py::test_f[x.py::y]", "q]/test_t.py::test_f"),
        ("tests/test_t.py::TestC::test_g[k]::m]", "tests/test_t.py::TestC::test_g"),
        ("odd[/test_t.py::test_f[a]]x.py::b]", "odd[/test_t.py::test_f"),
        ("odd[/test_t.py::test_f[]]].py::z]", "odd[/test_t.py::test_f"),
        ("odd[/test_t.py::TestC::test_h[a]]x.py::b]", "odd[/test_t.py::TestC::test_h"),
        ("w]x[/test_t.py::test_f[a]]x.py::b]", "w]x[/test_t.py::test_f"),
    ],
)
def test_failed_targets_find_a_case_id_with_brackets(failed, target):
    run = SimpleNamespace(outcomes={failed: "failed"})
    assert failed in pipeline_judge._failed_targets([run], frozenset({target}))


@pytest.mark.parametrize("case", ["test_f[a]::b]", "test_f[e]::f[g]"])
@pytest.mark.parametrize("path", ["tests/test_t.py", "tests/dir[1]/test_t.py", "q]/test_t.py"])
def test_passing_targets_see_a_changed_test_through_odd_case_ids(path, case):
    outcomes = {f"{path}::{case}": "passed", f"{path}::test_g[1]": "passed"}
    targets, _, left = p2p_nodes.passing_targets(
        outcomes, changed_files=frozenset({path}), changed=frozenset({f"{path}::test_f"})
    )
    assert left == 1 and targets == (f"{path}::test_g",)


@pytest.mark.parametrize("all_parts", [False, True])
@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("t.py::test_f[a]::b]", "t.py::test_f"),
        ("t.py::test_f[e]::f[g]", "t.py::test_f"),
        ("t.py::TestC::test_g[k]::m]", "t.py::TestC::test_g"),
        ("q]/t.py::test_f[x.py::y]", "q]/t.py::test_f"),
        ("dir[1]/test_a.py::test_x[case]", "dir[1]/test_a.py::test_x"),
        ("a::b/test_c.py::test_d", "a::b/test_c.py::test_d"),
        ("dir.pyx/tests/t.py::test_x[param]", "dir.pyx/tests/t.py::test_x"),
        ("a.pyc/t.py::C::test_x[p]", "a.pyc/t.py::C::test_x"),
        ("dir.py/t.py::test_x[a.py::b]", "dir.py/t.py::test_x"),
        ("tests/doc.txt::test_x[foo.py::bar]", "tests/doc.txt::test_x"),
        ("tests/t.py::test_y[x::y]", "tests/t.py::test_y"),
        ("u[v.py::w]/test_t.py::test_f[a]", "u[v.py::w]/test_t.py::test_f"),
        ("odd[/test_t.py::test_f[plain]", "odd[/test_t.py::test_f"),
        ("tests/dir[1]/test_x.py", "tests/dir[1]/test_x.py"),
        ("test_func[case]", "test_func"),
        ("odd[/test_t.py::test_f[a]]x.py::b]", "odd[/test_t.py::test_f"),
        ("odd[/test_t.py::test_f[]]].py::z]", "odd[/test_t.py::test_f"),
        ("odd[/test_t.py::TestC::test_h[a]]x.py::b]", "odd[/test_t.py::TestC::test_h"),
        ("w]x[/test_t.py::test_f[a]]x.py::b]", "w]x[/test_t.py::test_f"),
        ("odd[/test_t.py::test_f[x.py::y]", "odd[/test_t.py::test_f"),
    ],
)
def test_without_case_matches_origin_main(nodeid, expected, all_parts):
    assert without_case(nodeid, all_parts=all_parts) == expected


def test_a_name_without_a_case_variant_gives_no_parameter_hint():
    assert unmatched_note({"t.py::test_a[old]"}, {"t.py::test_a"}) == ""
