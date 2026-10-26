"""#283: ``cairnwork-verify run --import-path DIR`` lets a src-layout repo import its own package.

It reuses the Issue pipeline's ``import_paths`` (issue_task.check_import_paths,
TaskSpec.import_paths, the p2p base check and run_verifiers); the state config's ``import_paths``
is not read by verify.
"""

from typing import Any

import pytest
from conftest import commit_change
from verify_helpers import FIX, base_of, candidate, verify

from cairnwork import verify_candidate as vc
from cairnwork.intake import Intake
from cairnwork.pipeline import TaskRejected

sandbox = pytest.mark.sandbox
SRC_CMD = "python3 -m pytest -q tests/test_calc.py"
SRC_FILE = "src/mypkg/calc.py"
BUGGY = "def add(a, b):\n    return a + b + 1\n"
SRC_LAYOUT = {  # no conftest sys.path hack: the tests import the package from src/
    "src/mypkg/__init__.py": "",
    SRC_FILE: BUGGY,
    "tests/test_calc.py": (
        "from mypkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    ),
}


@pytest.fixture
def srcproject(repo):
    commit_change(repo, SRC_LAYOUT)
    return repo


def run(state, project, cand, *extra, allowed=(SRC_FILE,)):
    return verify(state, project, base_of(project), cand, *extra, allowed=allowed, tests=(SRC_CMD,))


@sandbox
def test_src_layout_is_uncomparable_without_the_flag_and_verified_with_it(state, srcproject):
    fix = candidate(srcproject, base_of(srcproject), {SRC_FILE: FIX})
    code, out = run(state, srcproject, fix)
    assert code == 2 and out["status"] == "UNCOMPARABLE" and out["verified"] is False
    assert out["uncomparable"]["code"] == "BASE_ENV_FAILED", out
    code, out = run(state, srcproject, fix, "--import-path", "src")
    assert (code, out["status"], out["verified"]) == (0, "VERIFIED", True)
    wrong = candidate(srcproject, base_of(srcproject), {SRC_FILE: "def add(a, b):\n    return 0\n"})
    code, out = run(state, srcproject, wrong, "--import-path", "src")
    assert (code, out["status"]) == (2, "FAILED")  # the base is comparable: a real failure


@pytest.mark.parametrize(
    "bad", ["../escape", "/abs", "tests", "tests/sub", "Tests", "TESTS/sub", "src/../x", "a*"]
)
def test_bad_import_path_is_refused_before_anything_runs(state, srcproject, bad):
    fix = candidate(srcproject, base_of(srcproject), {SRC_FILE: FIX})
    code, out = run(state, srcproject, fix, "--import-path", bad)
    assert code == 2 and out["error"] == "BAD_IMPORT_PATH" and set(out) == {"error", "detail"}
    assert not (state / "ownership.sqlite").exists()  # prepare() never ran


def test_default_taskspec_import_paths_is_empty(state, srcproject, monkeypatch):
    seen = []

    def spy(task, **kwargs):
        seen.append(task.import_paths)
        raise TaskRejected("STOP", "captured")

    monkeypatch.setattr(vc, "check_task", spy)
    fix = candidate(srcproject, base_of(srcproject), {SRC_FILE: FIX})
    kwargs: dict[str, Any] = dict(repo=srcproject, base=base_of(srcproject), candidate=fix)
    kwargs |= dict(allowed_files=(SRC_FILE,), verifiers=(("v", tuple(SRC_CMD.split())),))
    with pytest.raises(TaskRejected, match="captured"):
        vc.verify_candidate(Intake(state), **kwargs)
    with pytest.raises(TaskRejected, match="captured"):
        vc.verify_candidate(Intake(state), import_paths=("src",), **kwargs)
    assert seen == [(), ("src",)]


def test_repo_root_module_stays_protected_with_an_import_path(state, srcproject):
    fix = candidate(srcproject, base_of(srcproject), {SRC_FILE: FIX, "helper.py": "X = 1\n"})
    code, out = run(state, srcproject, fix, "--import-path", "src", allowed=(SRC_FILE, "helper.py"))
    assert code == 2 and out["error"] == "PROTECTED_VERIFIER_INPUT"


def test_a_shadowing_first_level_file_is_refused_on_the_verify_path(state, srcproject):
    # cairnwork-verify --import-path src must route import_paths into protected_verifier_inputs
    # (291 sets TaskSpec.import_paths; #290 passes it through check_task), so a candidate that may
    # add src/colorsys.py — shadowing stdlib colorsys a protected test could import — is refused
    # before anything runs.
    fix = candidate(srcproject, base_of(srcproject), {SRC_FILE: FIX})
    code, out = run(state, srcproject, fix, "--import-path", "src", allowed=("src/colorsys.py",))
    assert code == 2 and out["error"] == "PROTECTED_VERIFIER_INPUT", out
