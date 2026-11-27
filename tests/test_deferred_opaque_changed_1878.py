"""#1878: a changed test whose class shape cannot be read statically (an imported or aliased
base, a ``type()``-built class) is not covered by a node target: the file must be named whole.
Producer (pipeline_judge), receiver (verify_coverage) and the helpers they share."""

from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

from cairnwork.deferred_opaque import whole_file_required
from cairnwork.deferred_tests import ensure_test_commit, untargeted
from cairnwork_attest import verify_coverage as vc

sandbox = pytest.mark.sandbox
TEST = "tests/test_regression.py"
HEAD = "import unittest\nfrom unittest import TestCase as TC\nfrom calc import add\n"
HELPERS = "import unittest\n\n\nclass Base(unittest.TestCase):\n    pass\n"
SAME = "add(1, 1)"
VISIBLE = "def test_regression():\n    assert add(1, 1) == {n}\n"
SHAPES = {
    "import": "from helpers import Base\n\n\nclass Imp(Base):\n    def test_i(self):\n"
    "        assert add(1, 1) == {n}\n",
    "alias": "class Hid(TC):\n    def test_h(self):\n        assert add(1, 1) == {n}\n",
    "type": "def _t(self):\n    assert add(1, 1) == {n}\n\n\n"
    "Hid = type('Hid', (unittest.TestCase,), {{'test_h': _t}})\n",
}
NORMAL = {
    "plain": "class TestX:\n    def test_x(self):\n        assert add(1, 1) == {n}\n",
    "unittest": "class U(unittest.TestCase):\n    def test_u(self):\n"
    "        assert add(1, 1) == {n}\n",
    "alias_of_same_file": "class A(unittest.TestCase):\n    pass\n\n\nclass B(A):\n"
    "    def test_b(self):\n        assert add(1, 1) == {n}\n",
}


def _text(shape, n_visible, n_shape):
    return HEAD + VISIBLE.format(n=n_visible) + "\n\n" + shape.format(n=n_shape)


def _paths(project, files):
    base, commit = make_test_commit(project, files)
    ensure_test_commit(project, commit, origin_url=git(project, "remote", "get-url", "origin"))
    return base, commit


@pytest.mark.parametrize("name", SHAPES)
def test_unreadable_shape_changed_file_needs_whole(project, name):
    commit_change(project, {TEST: _text(SHAPES[name], SAME, SAME), "tests/helpers.py": HELPERS})
    base, commit = _paths(project, {TEST: _text(SHAPES[name], 2, 99)})
    assert whole_file_required(project, base, commit, (TEST,)) == {TEST}


@pytest.mark.parametrize("name", NORMAL)
def test_readable_shapes_need_no_whole(project, name):
    commit_change(project, {TEST: _text(NORMAL[name], SAME, SAME)})
    base, commit = _paths(project, {TEST: _text(NORMAL[name], 2, 99)})
    assert whole_file_required(project, base, commit, (TEST,)) == frozenset()


def test_new_file_with_unreadable_shape_needs_whole(project):
    base, commit = _paths(project, {TEST: _text(SHAPES["alias"], 2, 99)})
    assert whole_file_required(project, base, commit, (TEST,)) == {TEST}


def test_unchanged_file_needs_no_whole(project):
    commit_change(project, {TEST: _text(SHAPES["alias"], SAME, SAME)})
    base, commit = _paths(project, {"tests/test_other.py": "def test_o():\n    pass\n"})
    assert whole_file_required(project, base, commit, (TEST, "tests/data.json")) == frozenset()


def test_untargeted_whole_wants_the_file_named_whole():
    paths, nodes = (TEST,), {f"{TEST}::test_regression"}
    assert untargeted(paths, nodes, [TEST], whole={TEST}) == ()
    assert untargeted(paths, nodes, [f"{TEST}::test_regression"], whole={TEST}) == (TEST,)
    assert untargeted(paths, nodes, [f"{TEST}::test_regression"]) == ()  # default: unchanged
    assert untargeted(paths, nodes, [f"{TEST}::test_regression"], whole=()) == ()


def _argv(target):
    return (("pytest", ("python3", "-m", "pytest", "-q", target)),)


@sandbox
@pytest.mark.parametrize("name", SHAPES)
def test_node_target_cannot_hide_a_failing_changed_test(project, tmp_path, name):
    commit_change(project, {TEST: _text(SHAPES[name], SAME, SAME), "tests/helpers.py": HELPERS})
    base, commit = make_test_commit(project, {TEST: _text(SHAPES[name], 2, 99)})
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX),
                 verifiers=_argv(f"{TEST}::test_regression"))  # fmt: skip
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason


@sandbox
@pytest.mark.parametrize("name", SHAPES)
def test_whole_file_verifier_still_fails_on_the_candidate(project, tmp_path, name):
    commit_change(project, {TEST: _text(SHAPES[name], SAME, SAME), "tests/helpers.py": HELPERS})
    base, commit = make_test_commit(project, {TEST: _text(SHAPES[name], 2, 99)})
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status != "COMPLETED"
    assert not result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason


@sandbox
@pytest.mark.parametrize("name", SHAPES)
def test_whole_file_verifier_passes_when_the_changed_test_passes(project, tmp_path, name):
    commit_change(project, {TEST: _text(SHAPES[name], SAME, SAME), "tests/helpers.py": HELPERS})
    base, commit = make_test_commit(project, {TEST: _text(SHAPES[name], 2, 2)})
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status == "COMPLETED", result.reason


@sandbox
def test_receiver_refuses_a_node_target_for_an_unreadable_changed_file(project):
    commit_change(project, {TEST: _text(SHAPES["alias"], SAME, SAME)})
    base, commit = _paths(project, {TEST: _text(SHAPES["alias"], 2, 99)})
    verifier = ["v1", ["python3", "-m", "pytest", "-q", f"{TEST}::test_regression"]]
    binding = {"base": base, "test_commit": commit, "test_paths": [TEST], "verifiers": [verifier]}
    request = {"acceptance_contract": {"required_verifier_ids": ["v1"]}}
    ctx = SimpleNamespace(binding=binding, request=request, full=True, policy={}, repo=project)
    problem = vc.coverage_problem(ctx)
    assert problem is not None and problem.code == "TEST_PATH_NOT_TARGETED"
    binding["verifiers"] = [["v1", ["python3", "-m", "pytest", "-q", TEST]]]
    assert vc.coverage_problem(ctx) is None
