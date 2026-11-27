"""#1878 round 3: pytest's collected ids decide, not a list of class shapes. A changed test file
where pytest collects an id the static reader does not list must be named whole by a verifier
(DEFERRED_TESTS_NOT_TARGETED), whatever spelling built the hidden test. Every must-block and
must-pass input of the two review rounds, run end to end with a real ``run_task``."""

from pathlib import Path

import pytest
from conftest import commit_change
from test_deferred_opaque_changed_1878 import HEAD, TEST, VISIBLE, _argv
from test_deferred_tests import FIX, SeeingWorker, make_test_commit, run

from cairnwork import pipeline_judge
from cairnwork.collect_reader import Collected
from cairnwork.deferred_collected import NONE
from cairnwork.deferred_opaque import unlisted

sandbox = pytest.mark.sandbox
NODE = f"{TEST}::test_regression"
HELPERS = (
    "import unittest\n\n\nclass Base(unittest.TestCase):\n    pass\n\n\n"
    "class MyTestCase(unittest.TestCase):\n    pass\n\n\n"
    "def make(fn):\n    return type('Made', (unittest.TestCase,), {'test_h': fn})\n\n\n"
    "def deco(fn):\n    def wrap(cls):\n        cls.test_h = fn\n        return cls\n\n"
    "    return wrap\n"
)
FN = "def _t(self):\n    assert add(1, 1) == {n}\n\n\n"
T = "    def test_h(self):\n        assert add(1, 1) == {n}\n"
NS = "{{'test_h': _t}}"
ROUND2 = {  # review 2026-11-27T06:30Z: the nine spellings judged CORE_VERIFIED at f0cceffc
    "builder_by_name": "from helpers import make\n\n\n" + FN + "Hid = make(_t)\n",
    "builder_module_attr": "import helpers\n\n\n" + FN + "Hid = helpers.make(_t)\n",
    "imported_decorator": "from helpers import deco\n\n\n"
    + FN
    + "@deco(_t)\nclass U(unittest.TestCase):\n    pass\n",
    "decorator_rebinding": "from helpers import deco\n\n\n"
    + FN
    + "class U(unittest.TestCase):\n    pass\n\n\nU = deco(_t)(U)\n",
    "type_alias": FN + f"mk = type\nHid = mk('Hid', (unittest.TestCase,), {NS})\n",
    "builtins_type": "import builtins\n\n\n"
    + FN
    + f"Hid = builtins.type('Hid', (unittest.TestCase,), {NS})\n",
    "types_new_class": "import types\n\n\n"
    + FN
    + "Hid = types.new_class('Hid', (unittest.TestCase,), {{}}, lambda ns: ns.update(test_h=_t))\n",
    "type_star_args": FN + f"ARGS = ('Hid', (unittest.TestCase,), {NS})\nHid = type(*ARGS)\n",
    "type_name_rest": FN + f"REST = ((unittest.TestCase,), {NS})\nHid = type('Hid', *REST)\n",
}
ROUND1 = {  # review 2026-11-27T05:36Z: bold rows plus subscript and starred bases
    "module_attr": "import helpers\n\n\nclass Hid(helpers.Base):\n" + T,
    "module_alias_attr": "import helpers as h\n\n\nclass Hid(h.Base):\n" + T,
    "factory_class": "def make2():\n    class Hid(unittest.TestCase):\n        def test_h(self):\n"
    "            assert add(1, 1) == {n}\n\n    return Hid\n\n\nHid = make2()\n",
    "factory_type": FN + f"def make2():\n    return type('Hid', (unittest.TestCase,), {NS})\n\n\n"
    "Hid = make2()\n",
    "call_base": "def mk():\n    return unittest.TestCase\n\n\nclass Hid(mk()):\n" + T,
    "subscript_index": "BASES = (unittest.TestCase,)\n\n\nclass Hid(BASES[0]):\n" + T,
    "starred": "bs = (unittest.TestCase,)\n\n\nclass Hid(*bs):\n" + T,
}
MUST_BLOCK = {**ROUND2, **ROUND1}
READABLE = {  # must-pass: node targets naming the changed test decide on the candidate's run
    "unittest": "class U(unittest.TestCase):\n" + T,
    "pytest_class": "class TestX:\n" + T,
    "imported_testcase_name": "from helpers import MyTestCase\n\n\nclass U(MyTestCase):\n" + T,
}
READABLE_NODES = {
    "unittest": f"{TEST}::U::test_h",
    "pytest_class": f"{TEST}::TestX::test_h",
    "imported_testcase_name": f"{TEST}::U::test_h",
}


def _text(shape, n_visible, n_shape):
    return HEAD + VISIBLE.format(n=n_visible) + "\n\n" + shape.format(n=n_shape)


def _commits(project, shape, n_new):
    commit_change(project, {TEST: _text(shape, "1 + 1", "1 + 1"), "tests/helpers.py": HELPERS})
    return make_test_commit(project, {TEST: _text(shape, 2, n_new)})


@sandbox
@pytest.mark.parametrize("name", MUST_BLOCK)
def test_node_target_cannot_hide_a_failing_changed_test(project, tmp_path, name):
    base, commit = _commits(project, MUST_BLOCK[name], 99)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(NODE))
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason


@sandbox
@pytest.mark.parametrize("name", ROUND2)
def test_whole_file_verifier_fails_a_failing_changed_test(project, tmp_path, name):
    base, commit = _commits(project, ROUND2[name], 99)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status == "FAILED"
    assert not result.reason.startswith("DEFERRED_TESTS_"), result.reason


@sandbox
@pytest.mark.parametrize("name", ROUND2)
def test_whole_file_verifier_passes_a_passing_changed_test(project, tmp_path, name):
    base, commit = _commits(project, ROUND2[name], 2)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status == "COMPLETED", result.reason


@sandbox
@pytest.mark.parametrize("name", READABLE)
def test_readable_class_node_target_passes_a_correct_candidate(project, tmp_path, name):
    base, commit = _commits(project, READABLE[name], 2)
    targets = _argv(NODE) + (("named", ("python3", "-m", "pytest", "-q", READABLE_NODES[name])),)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=targets)
    assert result.status == "COMPLETED", result.reason


@sandbox
@pytest.mark.parametrize("name", READABLE)
def test_readable_class_node_target_fails_a_failing_changed_test(project, tmp_path, name):
    base, commit = _commits(project, READABLE[name], 99)
    targets = _argv(NODE) + (("named", ("python3", "-m", "pytest", "-q", READABLE_NODES[name])),)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=targets)
    assert result.status == "FAILED"
    assert result.reason.startswith("CORE_FAILED_VERIFICATION"), result.reason


def test_unlisted_names_files_whose_ids_are_unknown():
    got = Collected((), ("tests/a.py",), ("tests/b.py",))
    paths = ("tests/a.py", "tests/b.py", "tests/c.py")
    assert unlisted(Path("/no-repo"), "x", paths, got) == set(paths)  # c.py: no id, unknown too


NO_ID = {  # review 2026-11-27 (round 3): no usable collected id for the changed file
    "collect_failed": NONE,  # test commit and base probe both unreadable (_check returns NONE)
    "zero_ids": Collected(("tests/test_other.py::test_x",)),  # a record, none for this file
}


@sandbox
@pytest.mark.parametrize("record", NO_ID)
def test_no_collected_id_for_a_changed_file_needs_whole(project, tmp_path, monkeypatch, record):
    monkeypatch.setattr(
        pipeline_judge.deferred_collected, "check_with_base", lambda *a, **k: NO_ID[record]
    )
    base, commit = _commits(project, ROUND2["builder_by_name"], 99)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(NODE))
    assert result.status == "FAILED"
    assert result.reason.startswith("DEFERRED_TESTS_NOT_TARGETED"), result.reason


@sandbox
@pytest.mark.parametrize("record", NO_ID)
def test_no_collected_id_whole_file_verifier_still_decides(project, tmp_path, monkeypatch, record):
    monkeypatch.setattr(
        pipeline_judge.deferred_collected, "check_with_base", lambda *a, **k: NO_ID[record]
    )
    base, commit = _commits(project, ROUND2["builder_by_name"], 2)
    result = run(project, tmp_path, base, commit, SeeingWorker(FIX), verifiers=_argv(TEST))
    assert result.status == "COMPLETED", result.reason


def test_unlisted_names_a_changed_file_without_a_collected_id():
    got = Collected(("tests/b.py::test_x",))
    assert unlisted(Path("/no-repo"), "x", ("tests/a.py",), got) == {"tests/a.py"}
    assert unlisted(Path("/no-repo"), "x", ("tests/a.py",), NONE) == {"tests/a.py"}
