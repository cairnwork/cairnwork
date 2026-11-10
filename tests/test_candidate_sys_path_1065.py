"""Issue #1065: candidate-visible import changes must be refused before verification."""

from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_deferred_tests import make_test_commit
from verify_helpers import base_of, candidate, verify

from cairnwork import candidate_sys_path, pipeline_judge
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.workspace import CandidateRejected

sandbox = pytest.mark.sandbox


def test_candidate_sys_path_shadow_is_refused_before_deferred_tests_run(project, monkeypatch):
    base, test_commit = make_test_commit(
        project,
        {
            "tests/test_regression.py": (
                "import json\n"
                "from calc import add\n\n"
                "def test_fix_and_real_json():\n"
                "    assert add(2, 3) == 5\n"
                "    assert json.loads('{\"real\": true}') == {'real': True}\n"
            )
        },
    )
    candidate_source = (
        "import pathlib\n"
        "import sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'shadow'))\n"
        "def add(a, b):\n    return a + b\n"
    )
    cand = candidate(
        project,
        base,
        {
            "src/calc.py": candidate_source,
            "shadow/json.py": "def loads(value):\n    return {'real': True}\n",
        },
    )
    assert test_commit  # the deferred test imports json and requires real json behavior
    task = SimpleNamespace(repo=project, base_commit=base, blackbox=None)
    result = SimpleNamespace(candidate_commit=cand, task_id="task", attempt_id="attempt", logs=[])

    def verifier_must_not_run(*args, **kwargs):
        raise AssertionError("candidate verifier ran before the static import scan")

    monkeypatch.setattr(pipeline_judge, "run_verifiers", verifier_must_not_run)
    with pytest.raises(CandidateRejected) as got:
        pipeline_judge.judge_candidate(task, (), "digest", None, result, (), None, lambda _: None)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "src/calc.py" in str(got.value)


@sandbox
def test_plain_fix_remains_verified(project, state):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    code, out = verify(state, project, base, cand)
    assert code == 0 and out["status"] == "VERIFIED" and out["verified"] is True


def test_existing_base_signal_is_not_a_new_mutation(project):
    base = base_of(project)
    git(project, "checkout", "-q", "--detach", base)
    original = "import subprocess\n\ndef add(a, b):\n    return a + b + 1\n"
    commit_change(project, {"src/calc.py": original})
    base = git(project, "rev-parse", "HEAD")
    cand = candidate(
        project,
        base,
        {"src/calc.py": "import subprocess\n\ndef add(a, b):\n    return a + b\n"},
    )
    assert scan_sys_path_blob(git(project, "show", f"{base}:src/calc.py").encode()) is True
    assert scan_sys_path_blob(git(project, "show", f"{cand}:src/calc.py").encode()) is True
    candidate_sys_path.refuse_new_sys_path_mutation(project, base, cand)


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module('json')\n",
        "__import__('json')\n",
        "exec('import sys')\n",
        "import site\nsite.addsitedir('shadow')\n",
        "import os\nos.environ['PYTHONPATH'] = 'shadow'\n",
        "import sys\nsys.modules['json'] = object()\n",
        "import sys\nsys.meta_path.insert(0, object())\n",
    ],
)
def test_added_file_import_system_forms_are_detected(repo, source):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"new_module.py": source})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand)
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


def test_changed_blob_read_failure_is_closed(project, monkeypatch):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})

    def unreadable(*args, **kwargs):
        raise OSError("synthetic blob read failure")

    monkeypatch.setattr(candidate_sys_path, "run_git_bytes", unreadable)
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(project, base, cand)
    assert got.value.code == "UNREADABLE_CHANGE"


def test_candidate_parse_failure_is_closed(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"new_module.py": "import sys\nif:\n    pass\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand)
    assert got.value.code == "UNREADABLE_CHANGE"
