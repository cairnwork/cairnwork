"""Issue #1065: candidate-visible import changes must be refused before verification."""

from types import SimpleNamespace

import pytest
from conftest import commit_change, git
from test_deferred_tests import make_test_commit
from verify_helpers import base_of, candidate, verify

from cairnwork import candidate_sys_path, pipeline_judge
from cairnwork.sys_path_scan import scan_sys_path_blob
from cairnwork.verify_context import VerifyContext
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
    git(project, "fetch", "-q", "origin", test_commit)
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
    changed = git(project, "diff", "--name-only", base, cand).split()
    task = SimpleNamespace(
        repo=project,
        base_commit=base,
        test_commit=test_commit,
        blackbox=None,
        allowed_files=tuple(changed),
    )
    result = SimpleNamespace(candidate_commit=cand, task_id="task", attempt_id="attempt", logs=[])

    def verifier_must_not_run(*args, **kwargs):
        raise AssertionError("candidate verifier ran before the static import scan")

    monkeypatch.setattr(pipeline_judge, "run_verifiers", verifier_must_not_run)
    with pytest.raises(CandidateRejected) as got:
        pipeline_judge.judge_candidate(
            task, (), "digest", None, result, (), VerifyContext(project, project), lambda _: None
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "src/calc.py" in str(got.value)


@sandbox
def test_plain_fix_remains_verified(project, state):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})
    code, out = verify(state, project, base, cand)
    assert code == 0 and out["status"] == "VERIFIED" and out["verified"] is True, out


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
    candidate_sys_path.refuse_new_sys_path_mutation(project, base, cand, ("src/calc.py",))


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
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("new_module.py",))


SUBMODULE_PATH_REDIRECTS = {
    "__path__.insert": '__path__.insert(0, "shadow")\n',
    "__path__.append": '__path__.append("shadow")\n',
    "__path__ slice assignment": '__path__[:] = ["shadow"]\n',
    "__path__ replacement": '__path__ = ["shadow"]\n',
    "spec search locations": ('__spec__.submodule_search_locations.insert(0, "shadow")\n'),
}


@pytest.mark.parametrize("source", SUBMODULE_PATH_REDIRECTS.values(), ids=SUBMODULE_PATH_REDIRECTS)
@pytest.mark.parametrize("change_type", ["ADD", "MODIFY"])
def test_candidate_submodule_path_redirects_are_refused(repo, source, change_type):
    base = git(repo, "rev-parse", "HEAD")
    path = "pkg/__init__.py"
    if change_type == "MODIFY":
        base = commit_change(repo, {path: "VALUE = 1\n"})
    cand = commit_change(repo, {path: source})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, (path,))


def test_signal_and_nested_stdlib_module_are_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "src/a.py": "import sys\nsys.path.insert(0, 'outside')\n",
            "pkg/fractions.py": "def Fraction(*args): return 1\n",
        },
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "src/a.py" in str(got.value) and "pkg/fractions.py" in str(got.value)


@pytest.mark.parametrize("shadow", ["pkg/fractions.pyc", "pkg/fractions.abi3.so"])
def test_signal_and_nested_compiled_or_extension_shadow_are_refused(repo, shadow):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo, {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", shadow: ""}
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))
    assert shadow in str(got.value)


def test_signal_and_nested_test_import_package_are_refused(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit_change(repo, {"tests/test_x.py": "import helpers\n"})
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/helpers/__init__.py": ""},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/helpers/__init__.py" in str(got.value)


def test_harmless_nested_module_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/harmless.py": ""},
    )
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))


def test_root_stdlib_named_module_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "json.py": ""},
    )
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))


def test_shadow_file_without_signal_does_not_refuse(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"pkg/fractions.py": ""})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ())


def test_test_commit_import_can_define_shadow_name(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "pkg/helpers.py": ""},
    )
    test_commit = commit_change(repo, {"tests/test_x.py": "import helpers\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, ("src/a.py",), test_commit=test_commit
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"


def test_unparseable_candidate_test_file_fails_closed(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {"src/a.py": "import sys\nsys.path.insert(0, 'outside')\n", "tests/test_x.py": "if:\n"},
    )
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("src/a.py",))
    assert got.value.code == "UNREADABLE_CHANGE"


def test_package_path_redirect_hiding_in_a_shadow_module_is_refused_by_judge(project, monkeypatch):
    base, test_commit = make_test_commit(
        project,
        {
            "tests/test_regression.py": (
                "from pkg import helper\n\n"
                "def test_hidden_behavior():\n"
                "    assert helper.answer() == 'expected'\n"
            )
        },
    )
    git(project, "fetch", "-q", "origin", test_commit)
    candidate_source = (
        "import pathlib\n"
        "__path__.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'shadow'))\n"
    )
    cand = candidate(
        project,
        base,
        {
            "pkg/__init__.py": candidate_source,
            "shadow/helper.py": "def answer():\n    return 'expected'\n",
        },
    )
    changed = git(project, "diff", "--name-only", base, cand).split()
    task = SimpleNamespace(
        repo=project,
        base_commit=base,
        test_commit=test_commit,
        blackbox=None,
        allowed_files=tuple(changed),
    )
    result = SimpleNamespace(candidate_commit=cand, task_id="task", attempt_id="attempt", logs=[])

    def verifier_must_not_run(*args, **kwargs):
        raise AssertionError("candidate verifier ran before the static import scan")

    monkeypatch.setattr(pipeline_judge, "run_verifiers", verifier_must_not_run)
    with pytest.raises(CandidateRejected) as got:
        pipeline_judge.judge_candidate(
            task, (), "digest", None, result, (), VerifyContext(project, project), lambda _: None
        )
    assert got.value.code == "CANDIDATE_SYS_PATH_MUTATION"
    assert "pkg/__init__.py" in str(got.value)
    assert test_commit


def test_changed_blob_read_failure_is_closed(project, monkeypatch):
    base = base_of(project)
    cand = candidate(project, base, {"src/calc.py": "def add(a, b):\n    return a + b\n"})

    def unreadable(*args, **kwargs):
        raise OSError("synthetic blob read failure")

    monkeypatch.setattr(candidate_sys_path, "run_git_bytes", unreadable)
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(project, base, cand, ("src/calc.py",))
    assert got.value.code == "UNREADABLE_CHANGE"


def test_candidate_parse_failure_is_closed(repo):
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"new_module.py": "import sys\nif:\n    pass\n"})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("new_module.py",))
    assert got.value.code == "UNREADABLE_CHANGE"


def test_files_outside_the_candidates_allowed_files_are_not_its_mutation(repo):
    """On the worker path the judged commit also carries the deferred tests; those are the test
    commit's, read by sys_path_scan, not the candidate's (#1065, worker pipeline)."""
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(
        repo,
        {
            "tests/test_hidden.py": "import sys\nsys.path.insert(0, 'x')\n",
            "new_module.py": "VALUE = 1\n",
        },
    )
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("new_module.py",))
    candidate_sys_path.refuse_new_sys_path_mutation(
        repo, base, cand, ("new_module.py", "tests/test_hidden.py")
    )
