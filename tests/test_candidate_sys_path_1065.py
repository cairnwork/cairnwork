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


def _changed(repo, base, cand):
    """Every changed path as the candidate's allowed files (a shadow must be the candidate's)."""
    return tuple(git(repo, "diff", "--name-only", base, cand).split())


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


def test_existing_base_signal_without_a_shadow_is_not_refused(project):
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


MACHINERY_FORMS = [
    "import importlib\nimportlib.import_module('json')\n",
    "__import__('json')\n",
    "exec('import sys')\n",
    "import sys\nsys.modules['json'] = object()\n",
    "import sys\nsys.meta_path.insert(0, object())\n",
    "import sys\ngetattr(sys, 'mod' + 'ules')['json'] = object()\n",
]
REDIRECT_FORMS = [
    "import site\nsite.addsitedir('shadow')\n",
    "import os\nos.environ['PYTHONPATH'] = 'shadow'\n",
]


@pytest.mark.parametrize("source", MACHINERY_FORMS)
def test_in_process_machinery_edits_are_refused_without_a_shadow(repo, source):
    assert scan_sys_path_blob(source.encode()) is True
    base = git(repo, "rev-parse", "HEAD")
    cand = commit_change(repo, {"new_module.py": source})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, ("new_module.py",))
    assert "in-process" in str(got.value)


@pytest.mark.parametrize("source", REDIRECT_FORMS)
def test_redirect_forms_are_refused_only_next_to_a_shadow(repo, source):
    assert scan_sys_path_blob(source.encode()) is True
    base = git(repo, "rev-parse", "HEAD")
    alone = commit_change(repo, {"new_module.py": source})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, alone, ("new_module.py",))
    cand = commit_change(repo, {"shadow/json.py": ""})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "shadow/json.py" in str(got.value)


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
    """``from pkg import helper`` makes ``helper`` an imported name; an added ``shadow/helper.py``
    is the redirect target. Without that file the same edit is not refused."""
    commit_change(repo, {"tests/test_h.py": "from pkg import helper\n", "pkg/helper.py": ""})
    base = git(repo, "rev-parse", "HEAD")
    path = "pkg/__init__.py"
    if change_type == "MODIFY":
        base = commit_change(repo, {path: "VALUE = 1\n"})
    alone = commit_change(repo, {path: source})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, alone, _changed(repo, base, alone))
    cand = commit_change(repo, {"shadow/helper.py": ""})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "shadow/helper.py" in str(got.value)


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
    assert "shadow/helper.py" in str(got.value), got.value  # not the project's own src/calc.py


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
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
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


EXISTING_DYNAMIC = {
    "getattr": "def pick(o):\n    return getattr(o, 'a')\n",
    "re.compile": "import re\nimport subprocess\nPAT = re.compile('x')\n",
    "cursor.execute": "import subprocess\ndef run(cursor):\n    cursor.execute('select 1')\n",
}


@pytest.mark.parametrize("existing", EXISTING_DYNAMIC.values(), ids=EXISTING_DYNAMIC)
def test_a_one_line_fix_beside_unchanged_dynamic_access_is_not_refused(repo, existing):
    """Only new or changed statements decide the machinery class (#1111 review at c15173d)."""
    base = commit_change(repo, {"pkg/calc.py": existing + "def add(a, b):\n    return a + b + 1\n"})
    cand = commit_change(repo, {"pkg/calc.py": existing + "def add(a, b):\n    return a + b\n"})
    assert scan_sys_path_blob(git(repo, "show", f"{cand}:pkg/calc.py").encode()) is True
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


MACHINERY_EDITS = {
    "rewritten": (
        "import sys\nVALUE = getattr(sys, 'version')\n",
        "import sys\nVALUE = getattr(sys, 'mod' + 'ules')\n",
    ),
    "moved out of a dead function": (
        "import sys\ndef _unused():\n    sys.modules['json'] = object()\n",
        "import sys\nsys.modules['json'] = object()\ndef _unused():\n    pass\n",
    ),
    "dead base machinery called": (
        "import sys\ndef _swap():\n    sys.modules['json'] = object()\n",
        "import sys\ndef _swap():\n    sys.modules['json'] = object()\n_swap()\n",
    ),
    "called through another same-file def": (
        "import sys\ndef _swap():\n    sys.modules['json'] = object()\ndef _go():\n    _swap()\n",
        "import sys\ndef _swap():\n    sys.modules['json'] = object()\ndef _go():\n    _swap()\n"
        "_go()\n",
    ),
    "wrapping condition flipped": (
        "import sys\nif False:\n    sys.modules['json'] = object()\n",
        "import sys\nif True:\n    sys.modules['json'] = object()\n",
    ),
    "added beside an existing one": (
        "import sys\nVALUE = getattr(sys, 'version')\n",
        "import sys\nVALUE = getattr(sys, 'version')\nOTHER = getattr(sys, 'modules')\n",
    ),
}


@pytest.mark.parametrize(("old", "new"), MACHINERY_EDITS.values(), ids=MACHINERY_EDITS)
def test_a_new_or_changed_machinery_statement_is_refused(repo, old, new):
    base = commit_change(repo, {"pkg/calc.py": old})
    cand = commit_change(repo, {"pkg/calc.py": new})
    with pytest.raises(CandidateRejected) as got:
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
    assert "in-process" in str(got.value)


def test_an_unchanged_unparseable_fixture_does_not_refuse_an_honest_fix(repo):
    """A deliberately broken fixture the candidate did not touch imports nothing."""
    base = commit_change(
        repo, {"tests/fixtures/broken.py": "if:\n", "pkg/calc.py": "import subprocess\nX = 1\n"}
    )
    cand = commit_change(repo, {"pkg/calc.py": "import subprocess\nX = 2\n"})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_a_fix_calling_a_same_file_getattr_helper_is_not_refused(repo):
    """Same-file calls are followed into import machinery only (#1111 review at 225b181)."""
    helper = "import subprocess\ndef _scale():\n    return getattr(object, '__name__') and 1\n"
    base = commit_change(repo, {"pkg/calc.py": helper + "def half():\n    return (_scale(), 3)\n"})
    cand = commit_change(repo, {"pkg/calc.py": helper + "def half():\n    return (_scale(), 2)\n"})
    candidate_sys_path.refuse_new_sys_path_mutation(repo, base, cand, _changed(repo, base, cand))


def test_a_machinery_statement_moved_between_branches_is_refused(repo):
    """The block field is part of the key, so an if/else move is a change."""
    body = "    sys.modules['json'] = object()\n"
    base = commit_change(repo, {"pkg/calc.py": f"import sys\nif False:\n{body}else:\n    pass\n"})
    cand = commit_change(repo, {"pkg/calc.py": f"import sys\nif False:\n    pass\nelse:\n{body}"})
    with pytest.raises(CandidateRejected):
        candidate_sys_path.refuse_new_sys_path_mutation(
            repo, base, cand, _changed(repo, base, cand)
        )
