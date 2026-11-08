"""#1034: every test the base run ran must run on the candidate, under the same verifier.

A protected conftest may call project code while collecting. A candidate that changes that code
can make pytest stop collecting a test of a named file; until #1034 a whole-file target still
passed on the file's other tests, a base-failing test was not P2P's, and #697 counted any one
case of a parametrised new test. The base run's record (base code + deferred tests) is the list."""

import json
from types import SimpleNamespace

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from verify_helpers import candidate, verify

from cairnwork.deferred_overlay import TestFiles as Files
from cairnwork.pipeline_judge import _base_nodes_gone

sandbox = pytest.mark.sandbox
CONFTEST = (
    "import sys, pathlib\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
    "def pytest_collection_modifyitems(config, items):\n"
    "    import sel\n"
    "    sel.pick(items)\n"
    "    gone = [i for i in items if i.name == 'test_project_deselects']\n"  # the project's own
    "    items[:] = [i for i in items if i not in gone]\n"
    "    config.hook.pytest_deselected(items=gone)\n"
)
CALC_BASE = "def add(a, b):\n    return a + b + 1\ndef sub(a, b):\n    return a - b\n"
CALC_FIX = "def add(a, b):\n    return a + b\ndef sub(a, b):\n    return a - b\n"
EXISTING = (
    "from calc import add, sub\n"
    "def test_sub():\n    assert sub(3, 1) == 2\n"
    "def test_old_add():\n    assert add(2, 2) == 4\n"  # fails on the base (off by one)
    "def test_project_deselects():\n    assert False\n"
    "def test_stays():\n    pass\n"  # keeps the per-file rule (#697 era) quiet
)
KEEP = "def pick(items):\n    return None\n"
COMMAND = "python3 -m pytest -q tests/test_new.py tests/test_existing.py"
ALLOWED = ("src/calc.py", "src/sel.py")


def _drop(*names):
    return f"def pick(items):\n    items[:] = [i for i in items if i.name not in {names!r}]\n"


def _setup(project, state, new_test, p2p="off"):
    commit_change(project, {"tests/conftest.py": CONFTEST, "src/calc.py": CALC_BASE,
                            "src/sel.py": KEEP, "tests/test_existing.py": EXISTING})  # fmt: skip
    config = json.loads((state / "config.json").read_text())
    (state / "config.json").write_text(json.dumps({**config, "p2p_selection": p2p}))
    return make_test_commit(project, {"tests/test_new.py": new_test})


def _verify(project, state, base, test_commit, files):
    cand = candidate(project, base, files)
    _, out = verify(state, project, base, cand, "--test-commit", test_commit,
                    allowed=ALLOWED, tests=(COMMAND,))  # fmt: skip
    return out


NEW = "from calc import add\ndef test_new():\n    assert add(1, 1) == 2\n"
WEAK = "from calc import add\ndef test_new():\n    assert add(1, 1) != 3\n"  # only "not the base"
PARAM = (
    "import pytest\nfrom calc import add\n"
    "@pytest.mark.parametrize('a', [1, 2])\ndef test_new(a):\n    assert add(a, a) == 2 * a\n"
)


@sandbox
def test_a_correct_candidate_under_a_selecting_conftest_is_verified(project, state):
    base, test_commit = _setup(project, state, NEW)
    out = _verify(project, state, base, test_commit, {"src/calc.py": CALC_FIX})
    assert out["verified"] is True, out


@sandbox
def test_an_unfixed_candidate_still_fails(project, state):
    base, test_commit = _setup(project, state, NEW)
    out = _verify(project, state, base, test_commit, {"src/sel.py": KEEP + "# touched\n"})
    assert out["verified"] is False and out["status"] == "FAILED", out


@sandbox
def test_dropping_a_base_passing_test_is_not_verified_with_p2p_off(project, state):
    base, test_commit = _setup(project, state, NEW)
    wrong = CALC_FIX.replace("a - b", "a + b")  # breaks sub, then hides its test
    files = {"src/calc.py": wrong, "src/sel.py": _drop("test_sub", "test_old_add")}
    out = _verify(project, state, base, test_commit, files)
    assert out["verified"] is False, out
    assert out["reason"].startswith("NAMED_TESTS_NOT_RUN: 2 test(s)"), out
    assert "tests/test_existing.py::test_sub" in out["reason"]


@sandbox
def test_dropping_a_base_failing_test_is_not_verified_with_p2p_on(project, state):
    base, test_commit = _setup(project, state, WEAK, p2p="whole-suite")
    wrong = CALC_BASE.replace("a + b + 1", "a + b + 2")  # wrong, passes only the weak test
    files = {"src/calc.py": wrong, "src/sel.py": _drop("test_old_add")}
    out = _verify(project, state, base, test_commit, files)
    assert out["verified"] is False, out
    assert out["reason"].startswith("NAMED_TESTS_NOT_RUN: 1 test(s)"), out
    assert "tests/test_existing.py::test_old_add" in out["reason"]


@sandbox
def test_dropping_one_case_of_a_new_parametrised_test_is_not_verified(project, state):
    base, test_commit = _setup(project, state, PARAM)
    wrong = CALC_FIX.replace("return a + b\n", "return a + b if a == 1 else 0\n", 1)
    files = {"src/calc.py": wrong, "src/sel.py": _drop("test_new[2]", "test_old_add")}
    out = _verify(project, state, base, test_commit, files)
    assert out["verified"] is False, out
    assert out["reason"].startswith("NAMED_TESTS_NOT_RUN: 2 test(s)"), out
    assert "tests/test_new.py::test_new[2]" in out["reason"]


@sandbox
def test_skipping_instead_of_dropping_is_not_verified(project, state):
    """A pin, not a discriminator: a skip is not a pass on main either (report_guard, #44)."""
    base, test_commit = _setup(project, state, NEW)
    wrong = CALC_FIX.replace("a - b", "a + b")
    skip = (
        "import pytest\ndef pick(items):\n    for i in items:\n"
        "        if i.name == 'test_sub':\n            i.add_marker(pytest.mark.skip)\n"
    )
    out = _verify(project, state, base, test_commit, {"src/calc.py": wrong, "src/sel.py": skip})
    assert out["verified"] is False, out


def _run(verifier_id, outcomes):
    return SimpleNamespace(spec=SimpleNamespace(verifier_id=verifier_id), outcomes=outcomes)


def test_gone_compares_full_ids_per_verifier():
    case = "tests/t.py::test_x[2]"
    files = Files(("tests/t.py",), base_nodes={"v": ("tests/t.py::test_x[1]", case)})
    one_case = _run("v", {"tests/t.py::test_x[1]": "passed"})
    assert _base_nodes_gone(files, [one_case]) == [case]  # the other case does not stand in
    elsewhere = _run("w", {case: "passed", "tests/t.py::test_x[1]": "passed"})
    assert _base_nodes_gone(files, [one_case, elsewhere]) == [case]  # another verifier neither
    more = _run("v", {"tests/t.py::test_x[1]": "passed", case: "failed", "tests/t.py::y": "passed"})
    assert _base_nodes_gone(files, [more]) == []  # a superset; the outcome is judged elsewhere


def test_no_base_record_adds_no_requirement():
    assert _base_nodes_gone(Files(("tests/t.py",)), [_run("v", {})]) == []
    assert _base_nodes_gone(("tests/t.py",), [_run("v", {})]) == []  # a plain tuple (no tests)
