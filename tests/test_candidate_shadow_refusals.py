"""A candidate that leaves the bug in place cannot reach a pass by adding files that pytest or the
interpreter would pick up, or by writing them into the checkout while it is imported.

Each case keeps ``add`` wrong, so the protected test fails; the case shows which layer says no:
the task check (``PROTECTED_VERIFIER_INPUT``), the scope check (``OUT_OF_SCOPE``), or the verifier
itself (pinned ini, ``-P``, the guard's own record, the final checkout comparison). Startup hooks
(``sitecustomize``, ``usercustomize``, ``.pth``) and a root module named like the standard library
are stopped by the task and scope checks; in the checkout they would not run at all, so no verifier
case claims them. The task and scope cases need no sandbox and run on every host; only the cases
that run the verifier carry the ``sandbox`` mark. Cases already
pinned elsewhere are not repeated here: a root ``pytest.py`` (test_verifiers.py,
test_root_modules.py), a candidate ``tests/conftest.py`` and ``setup.cfg`` or ``pytest.ini``
addopts (test_verifiers.py), ``os._exit`` at import (test_verifiers.py, test_verify_candidate.py).
"""

import sys

import pytest
from conftest import git
from verify_helpers import base_of, candidate, verify

from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

sandbox = pytest.mark.sandbox

TARGET = "tests/test_calc.py"
FAILED = {f"{TARGET}::test_add": "failed"}
WRONG = "def add(a, b):\n    return 1 + a + b\n"  # still wrong: the base has a + b + 1
EXIT = "import os\ngetattr(os, '_ex' + 'it')(0)\n"  # name built from strings: past tamper_scan
COLLECT_ONLY = "[pytest]\naddopts = --collect-only\n"
NO_TESTS = '[pytest]\npython_functions = ["nothing_*"]\n'  # not an addopts: only -c keeps it out
SESSION_ZERO = "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n"


def _run(project, tmp_path, files):
    head = git(project, "rev-parse", "HEAD")
    commit = candidate(project, head, {"src/calc.py": WRONG, **files})
    spec = parse_verifier("v", ["python3", "-m", "pytest", "-q", TARGET])
    return run_verifiers(project, commit, (spec,), tmp_path / "runs", "r1", python=sys.executable)[
        0
    ]


def _task(project, path):
    verifier = (("v", ("python3", "-m", "pytest", "-q", TARGET)),)
    base = base_of(project)
    return TaskSpec("key", "card", "title", "fix", project, base, (path,), verifier)


@pytest.mark.parametrize(
    "path",
    [
        "pytest/__init__.py",
        "sitecustomize.py",
        "usercustomize.py",
        "startup.pth",
        "src/conftest.py",
        "pdb.py",
    ],
)
def test_a_task_naming_a_file_that_shadows_pytest_startup_or_stdlib_is_refused(project, path):
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(_task(project, path))


@pytest.mark.parametrize(
    "files",
    [
        {"pytest/__init__.py": EXIT, "pytest/__main__.py": EXIT},
        {"pytest.toml": NO_TESTS},
        {"pytest.toml": '[pytest]\naddopts = ["--collect-only"]\n'},
        {".pytest.ini": COLLECT_ONLY},
        {"tox.ini": COLLECT_ONLY},
        {"pyproject.toml": '[tool.pytest.ini_options]\naddopts = "--collect-only"\n'},
    ],
    ids=[
        "pytest-package",
        "pytest-toml-test-names",
        "pytest-toml",
        "dot-pytest-ini",
        "tox-ini",
        "pyproject",
    ],
)
@sandbox
def test_a_pytest_package_or_config_in_the_checkout_does_not_change_the_run(
    project, tmp_path, files
):
    """Even past the task and scope checks, the real pytest runs the protected test and it fails.

    The package is kept out by ``-P``. A config file is kept out by the pinned ini (``-c``); for
    the ``addopts`` cases ``-o addopts=`` is a second, redundant layer, so those go red only when
    both are removed, while ``pytest-toml-test-names`` goes red when ``-c`` alone is removed."""
    got = _run(project, tmp_path, files)
    assert not got.observation.passed and got.returncode == 1
    assert got.outcomes == FAILED and got.plain_failure


@sandbox
def test_a_root_conftest_that_zeroes_the_exit_status_is_not_a_pass(project, tmp_path):
    got = _run(project, tmp_path, {"conftest.py": SESSION_ZERO})
    assert got.returncode == 0 and not got.observation.passed
    assert got.outcomes == FAILED and "guard saw no test run" in got.why


@sandbox
@pytest.mark.parametrize("where", ["src/pdb.py", "src/calendar.py"])
def test_a_module_named_like_the_standard_library_that_exits_is_not_a_pass(
    project, tmp_path, where
):
    """src/ is on the tests' import path (the fixture's tests/conftest.py), and a task that does not
    declare it as an import path may name a file there: the guard's missing record says no."""
    name = where.rsplit("/", 1)[-1].removesuffix(".py")
    got = _run(project, tmp_path, {where: EXIT, "src/calc.py": f"import {name}\n" + WRONG})
    assert got.returncode == 0 and not got.observation.passed
    assert got.outcomes == {} and TARGET in got.why


_ROOT = "import os, pathlib\n_root = pathlib.Path(__file__).resolve().parent.parent\n"
PLANTS = {
    "conftest": (
        "for _d in (_root, _root / 'tests'):\n"
        f"    (_d / 'conftest.py').write_text({SESSION_ZERO!r})\n",
        ("extra conftest.py", "changed tests/conftest.py"),
    ),
    "pytest-py": (
        "(_root / 'pytest.py').write_text('import sys\\nsys.exit(0)\\n')\n",
        ("extra pytest.py",),
    ),
    "pytest-ini": (
        f"(_root / 'pytest.ini').write_text({COLLECT_ONLY!r})\n",
        ("extra pytest.ini",),
    ),
    "symlink": (
        "os.symlink('/dev/null', _root / 'tests' / 'link.py')\n",
        ("extra tests/link.py",),
    ),
}


@sandbox
@pytest.mark.parametrize("name", list(PLANTS))
def test_a_file_written_into_the_checkout_at_import_fails_the_run(project, tmp_path, name):
    plant, expected = PLANTS[name]
    got = _run(project, tmp_path, {"src/calc.py": _ROOT + plant + WRONG})
    assert not got.observation.passed and got.outcomes == FAILED
    assert set(expected) <= set(got.mutated)


@sandbox
def test_a_conftest_written_at_import_ends_failed_end_to_end(project, state):
    plant, _ = PLANTS["conftest"]
    base = base_of(project)
    code, out = verify(
        state, project, base, candidate(project, base, {"src/calc.py": _ROOT + plant + WRONG})
    )
    assert code == 2 and out["verified"] is False and out["status"] == "FAILED"
    assert "checkout changed" in out["reason"] and out["core_status"] == "FAILED_VERIFICATION"


@pytest.mark.parametrize(
    "path",
    ["pytest.py", "pytest/__init__.py", "conftest.py", "sitecustomize.py", "startup.pth", "pdb.py"],
)
def test_a_file_outside_the_allowed_files_is_refused_before_it_runs(project, state, path):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": WRONG, path: EXIT})
    code, out = verify(state, project, base, cand)
    assert code == 2 and out["status"] == "REFUSED" and out["reason"] == f"OUT_OF_SCOPE: {path}"


@sandbox
def test_a_relative_repository_path_still_fails_a_wrong_candidate(project, state, monkeypatch):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": WRONG})
    monkeypatch.chdir(project.parent)
    code, out = verify(state, project.relative_to(project.parent), base, cand)
    assert code == 2 and out["status"] == "FAILED" and out["core_status"] == "FAILED_VERIFICATION"
