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
