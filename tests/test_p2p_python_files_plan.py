"""#877: the name match follows python_files only when the base sets its own; the default keeps
main's exact names (R1), the plan and base_env_check pass the rule on (end to end), and a name
part is bounded (myapp is not app)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import commit_change
from p2p_side_stub import stub_side
from test_p2p_plan import RESULT, TASK

from cairnwork import p2p
from cairnwork.p2p_selection import name_rule
from cairnwork.p2p_selection import select_p2p_targets as select
from cairnwork.verify_context import VerifyContext

ROOTS = ("tests",)
R1_TESTS = ("tests/sub/test_calc_extra.py", "tests/test_app.py", "tests/test_misc.py")
ROOT_LEVEL = ("tests/test_app.py", "tests/test_misc.py")
SUB = ("tests/sub/check_calc.py", "tests/sub/check_other.py")


@pytest.mark.parametrize(
    "ini",
    [
        "[pytest]\n",
        "[pytest]\npython_files = test_*.py *_test.py\n",
        "[pytest]\npython_files = test_*.py\n",
    ],
)
def test_the_default_python_files_select_exactly_what_main_selects(repo, ini):
    # R1: test_calc_extra.py holds the stem "calc" loosely, but main never counts it as named
    # after calc.py, so the root-level fallback still applies and the selection equals main's
    base = commit_change(repo, {"pytest.ini": ini})
    changed = ("src/calc.py",)
    on_main = select(changed, R1_TESTS, ROOTS, "module-dir")
    assert on_main == ROOT_LEVEL
    assert select(changed, R1_TESTS, ROOTS, "module-dir", name_rule(repo, base)) == on_main


@pytest.fixture
def wired(repo, tmp_path, monkeypatch):
    """A module-dir task whose base sets python_files = check_*.py; probes stubbed and recorded."""
    tests = (*SUB, "tests/test_x.py")
    base = commit_change(repo, {"pytest.ini": "[pytest]\npython_files = check_*.py\n"})
    task = SimpleNamespace(**{**vars(TASK), "repo": repo, "base_commit": base})
    task.allowed_files, task.p2p_mode, task.test_commit = ("src/calc.py",), "module-dir", "t" * 40
    task.p2p_budget = 30
    probed: list[tuple[str, ...]] = []

    def fake_probe(task, files, result, ctx, budget, tag="p"):
        probed.append(files)
        return {f"{files[0]}::t": "passed"}, ""

    stub_side(monkeypatch)
    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: tests)
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    monkeypatch.setattr(p2p, "_env_fingerprint", lambda python: None)
    return task, probed, VerifyContext.of(task, tmp_path, RESULT.attempt_id)


def test_plan_p2p_selects_by_the_custom_python_files_of_the_base(wired):
    task, probed, ctx = wired
    got = p2p.plan_p2p(task, (), (), "module-dir", 30, RESULT, ctx)
    assert isinstance(got, p2p.P2PPlan) and probed == [SUB]


def test_base_env_check_probes_by_the_custom_python_files_of_the_base(wired):
    task, probed, ctx = wired
    p2p.base_env_check(task, None, ctx)
    assert probed == [SUB]


@pytest.mark.parametrize(
    ("test", "named"),
    [
        ("tests/sub/test_myapp.py", False),  # myapp is another word: not app
        ("tests/sub/myapp_test.py", False),
        ("tests/sub/test_app2.py", False),
        ("tests/sub/test_my_app.py", True),
        ("tests/sub/test_app_v2.py", True),
        ("tests/sub/x_app.py", True),
    ],
)
def test_a_name_part_is_bounded_a_longer_word_is_not_a_hit(repo, test, named):
    ini = "[pytest]\npython_files = test_*.py *_test.py x_*.py *_app.py\n"
    rule = name_rule(repo, commit_change(repo, {"pytest.ini": ini}))
    got = select(("src/app.py",), (test, "tests/test_x.py"), ROOTS, "module-dir", rule)
    # a hit selects the test's directory; no hit falls back to the root-level tests
    assert got == ((test,) if named else ("tests/test_x.py",))
