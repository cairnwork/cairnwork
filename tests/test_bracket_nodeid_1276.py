from types import SimpleNamespace

from cairnwork.base_teeth import base_teeth
from cairnwork.deferred_tests import _without_case, not_run


def test_without_case_keeps_parent_and_function_after_bracketed_parent():
    nodeid = "t.py::C[a::b]::test_y[param]"
    assert _without_case(nodeid) == "t.py::C[a::b]::test_y"


def test_not_run_matches_bracketed_directory_target():
    target = "tests[x]/t.py::test_y"
    run = SimpleNamespace(outcomes={target: "passed"})
    assert not_run([run], {target}) == []


def test_base_teeth_counts_failure_in_bracketed_directory():
    target = "tests[x]/t.py::test_y"
    run = SimpleNamespace(outcomes={target: "failed"}, fail_kind={})
    assert base_teeth([run], {target}) == ("TEETH", "", "")


def test_base_teeth_matches_parametrized_class_target():
    nodeid = "t.py::C[variant]::test_y"
    run = SimpleNamespace(outcomes={nodeid: "failed"}, fail_kind={})
    assert base_teeth([run], {"t.py::C::test_y"}) == ("TEETH", "", "")


def test_not_run_matches_parametrized_class_target():
    nodeid = "t.py::C[variant]::test_y"
    run = SimpleNamespace(outcomes={nodeid: "passed"})
    assert not_run([run], {"t.py::C::test_y"}) == []


def test_untargeted_bracketed_parent_does_not_cover_class():
    from cairnwork.deferred_tests import untargeted

    assert untargeted(
        ("t.py",),
        ("t.py::C[a::b]::test_y",),
        ("t.py::C",),
    ) == ("t.py",)
