"""#866 case A2: on the verify path an allowed file the candidate leaves unchanged is dropped
before the shadow rules run, so an unchanged module named like a stdlib module no longer refuses
the task. A changed one still does, and a change outside the list is still OUT_OF_SCOPE."""

import json

import pytest
from conftest import commit_change
from test_deferred_tests import make_test_commit
from verify_helpers import base_of, candidate, verify

from cairnwork.pipeline import TaskSpec
from cairnwork.verify_scope import narrowed

sandbox = pytest.mark.sandbox
CMD = "python3 -m pytest -q pkg/tests/test_calc.py"
ALLOW = ("pkg/__init__.py", "pkg/fractions.py")
# pkg is a package above the test root pkg/tests; fractions.py is named like a stdlib module
BASE = {
    "pkg/__init__.py": "VALUE = 1\n",
    "pkg/core.py": "def f():\n    return 1\n",
    "pkg/fractions.py": "PWNED = 1\n",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_calc.py": "from pkg import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
}


@pytest.fixture
def pkg_project(repo, state):
    commit_change(repo, BASE)
    config = {"verifier_account": False, "test_roots": ["pkg/tests"]}
    (state / "config.json").write_text(json.dumps(config))
    return repo


def _code(out):
    return out.get("error") or out.get("status")


@sandbox
def test_an_unchanged_stdlib_named_file_in_the_list_no_longer_refuses(state, pkg_project):
    base = base_of(pkg_project)
    fix = candidate(pkg_project, base, {"pkg/__init__.py": "VALUE = 2\n"})
    code, out = verify(state, pkg_project, base, fix, allowed=ALLOW, tests=(CMD,))
    assert out.get("status") == "VERIFIED" and out.get("verified") is True, out


def test_a_changed_stdlib_named_file_is_still_refused(state, pkg_project):
    base = base_of(pkg_project)
    both = candidate(
        pkg_project, base, {"pkg/__init__.py": "VALUE = 2\n", "pkg/fractions.py": "PWNED = 2\n"}
    )
    code, out = verify(state, pkg_project, base, both, allowed=ALLOW, tests=(CMD,))
    assert _code(out) == "PROTECTED_VERIFIER_INPUT", out


def test_a_change_outside_the_list_is_still_out_of_scope(state, pkg_project):
    base = base_of(pkg_project)
    out_of_list = candidate(
        pkg_project,
        base,
        {"pkg/__init__.py": "VALUE = 2\n", "pkg/core.py": "def f():\n    return 2\n"},
    )
    code, out = verify(state, pkg_project, base, out_of_list, allowed=ALLOW, tests=(CMD,))
    assert out.get("verified") is not True, out
    assert "OUT_OF_SCOPE" in json.dumps(out), out


def test_nothing_listed_changed_keeps_todays_refusal(state, pkg_project):
    base = base_of(pkg_project)
    other = candidate(pkg_project, base, {"pkg/core.py": "def f():\n    return 2\n"})
    code, out = verify(state, pkg_project, base, other, allowed=ALLOW, tests=(CMD,))
    assert _code(out) == "PROTECTED_VERIFIER_INPUT", out


def _task(repo, base, files):
    return TaskSpec("k", "verify", "verify", "", repo, base, tuple(files), (("v", ("python3",)),))


def test_the_list_is_kept_when_git_cannot_tell(pkg_project):
    base = base_of(pkg_project)
    for bad in ("HEAD", "z" * 40, base, "0" * 40, "--output=/tmp/x" + "0" * 26):
        assert narrowed(_task(pkg_project, base, ALLOW), bad).allowed_files == ALLOW


def test_a_malformed_list_is_kept_whole_so_check_task_still_refuses_it(pkg_project):
    base = base_of(pkg_project)
    fix = candidate(pkg_project, base, {"pkg/__init__.py": "VALUE = 2\n"})
    init = "pkg/__init__.py"
    for files in (
        (init, init),  # duplicates, one of them changed
        ("pkg/fractions.py", "pkg/fractions.py", init),  # the unchanged entry is the duplicate
        (init, ".github/workflows/ci.yml"),  # a protected path, unchanged
        (init, "./pkg/fractions.py"),
        (init, "pkg/"),
        (init, "/abs/path.py"),
        (init, "../up.py"),
        tuple(f"pkg/m{i}.py" for i in range(10)) + (init,),  # 11 entries
    ):
        assert narrowed(_task(pkg_project, base, files), fix).allowed_files == files, files


def test_only_check_task_sees_the_narrowed_list(pkg_project):
    base = base_of(pkg_project)
    fix = candidate(pkg_project, base, {"pkg/__init__.py": "VALUE = 2\n"})
    task = _task(pkg_project, base, ALLOW)
    assert narrowed(task, fix).allowed_files == ("pkg/__init__.py",)
    assert task.allowed_files == ALLOW  # P2P, the receipt and the candidate check keep the list


def test_an_unchanged_protected_path_in_the_list_is_still_refused(state, pkg_project):
    base = base_of(pkg_project)
    fix = candidate(pkg_project, base, {"pkg/__init__.py": "VALUE = 2\n"})
    allowed = ("pkg/__init__.py", ".github/workflows/ci.yml")
    code, out = verify(state, pkg_project, base, fix, allowed=allowed, tests=(CMD,))
    assert _code(out) == "TASK_ALLOWED_FILES_PROTECTED", out


P2P_BASE = {
    "src/alpha.py": (
        "def a():\n    return 1\n\ndef helper():\n    return 10\n\ndef other():\n    return 3\n"
    ),
    "src/beta.py": "from alpha import helper\n\ndef b():\n    return helper() + 1\n",
    "tests/conftest.py": (
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
    ),
    "tests/ta/test_alpha.py": (
        "from alpha import other\n\ndef test_other():\n    assert other() == 3\n"
    ),
    "tests/tb/test_beta.py": "from beta import b\n\ndef test_b():\n    assert b() == 11\n",
}
FIXED_A_BROKEN_HELPER = (
    "def a():\n    return 2\n\ndef helper():\n    return 0\n\ndef other():\n    return 3\n"
)


@sandbox
def test_p2p_module_dir_still_runs_tests_near_a_listed_unchanged_module(repo, state):
    """beta.py is listed but unchanged; the candidate breaks the helper beta uses. The test
    next to beta must still run and fail the candidate: only the shadow gate sees the narrowed
    list, the run keeps the task's own list (test drafted by reviewer a1e3)."""
    commit_change(repo, P2P_BASE)
    (state / "config.json").write_text(
        json.dumps({"verifier_account": False, "p2p_selection": "module-dir"})
    )
    fixed = "from alpha import a\n\ndef test_a_fixed():\n    assert a() == 2\n"
    base, tc = make_test_commit(repo, {"tests/ta/test_a_fixed.py": fixed})
    bad = candidate(repo, base, {"src/alpha.py": FIXED_A_BROKEN_HELPER})
    _code, out = verify(
        state,
        repo,
        base,
        bad,
        "--test-commit",
        tc,
        allowed=("src/alpha.py", "src/beta.py"),
        tests=("python3 -m pytest -q tests/ta/test_a_fixed.py",),
    )
    assert out.get("status") == "FAILED", out
    assert "p2p-module-dir" in str(out.get("reason", "")), out
    assert out["p2p"]["base_ran"] == 2, out["p2p"]
