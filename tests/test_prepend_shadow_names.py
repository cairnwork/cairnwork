"""#360, PR #376 review round 2: a directory above a test root against every name the tests see.

pytest's prepend import mode can put a directory above a test root (``pkg``) at the front of
sys.path once an ``__init__.py`` above the root is deleted or the test commit makes the root a
package. Anything in it named like a module the tests import then replaces that module: not only
the standard library or an installed distribution, but also the project's own modules at the
checkout root or under an import path. And a namespace directory that gains an ``__init__.py``
wins over a module of that name later on sys.path. The gate (verifier_spec) refuses both before
anything is bound.
"""

import sys

import pytest
from conftest import commit_change, git
from test_base_pythonpath_shadow import PKG_VERIFY, VERIFY, _payload, _route, _state

from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

PACKAGE = {  # a package root inside a package, and a subpackage named like the root's calc.py
    "calc.py": "def half():\n    return 0\n",
    "pkg/__init__.py": "",
    "pkg/tests/__init__.py": "",
    "pkg/tests/test_calc.py": "import calc\n\n\ndef test_half():\n    assert calc.half() == 0.5\n",
    "pkg/calc/__init__.py": "from .impl import *\n",
    "pkg/calc/impl.py": "def half():\n    return 0\n",
}
CASES = {  # base files changed from PACKAGE, the candidate's paths, the state's import paths
    "a subpackage named like a root module": ({}, ["pkg/calc/impl.py"], []),
    "the module under an import path": (
        {"calc.py": None, "src/calc.py": "x = 1\n"}, ["pkg/calc/impl.py"], ["src"]),
    "a module file above the root": ({"pkg/calc.py": "x = 1\n"}, ["pkg/calc.py"], []),
    "a case variant": ({}, ["pkg/CALC/impl.py"], []),
    "a capitalised module name": ({"calc.py": None, "pkg/calc/__init__.py": None,
                                   "pkg/calc/impl.py": None, "Calc.py": "x = 1\n",
                                   "pkg/Calc/impl.py": "x = 1\n"}, ["pkg/Calc/impl.py"], []),
    "a test helper's name": ({"pkg/tests/sub/helpers.py": "x = 1\n",
                              "pkg/helpers/impl.py": "x = 1\n"}, ["pkg/helpers/impl.py"], []),
}  # fmt: skip


def _base(repo, changes):
    files = {**PACKAGE, **changes}  # None: the base does not have that file
    return commit_change(repo, {path: text for path, text in files.items() if text is not None})


def _both(repo, tmp_path, monkeypatch, capsys, head, paths, import_paths, roots, verify):
    config = {"test_roots": roots, "import_paths": import_paths}
    state = _state(tmp_path, monkeypatch, capsys, accepting=False, **config)
    payload = _payload(repo, head, paths[0]) | {
        "allowed_files": paths,
        "verifiers": [["pytest", list(verify[0][1])]],
    }
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    return [(code, out.get("error")) for code, out in answers]


REFUSED = [(2, "PROTECTED_VERIFIER_INPUT"), (2, "PROTECTED_VERIFIER_INPUT")]
ALLOWED = [(0, None), (2, "NOT_ACCEPTING")]  # submit stops only because intake is drained


# Each way pkg gets in front of sys.path. PACKAGE alone keeps it a sealed package (#382): pytest
# then inserts the checkout root, never pkg, and none of these names can shadow anything.
ROUTES = {
    "pkg/__init__.py deleted": lambda changes, paths: (changes, ["pkg/__init__.py", *paths]),
    "the test commit adds pkg/tests/__init__.py": lambda changes, paths: (
        {**changes, "pkg/__init__.py": None, "pkg/tests/__init__.py": None}, paths),
}  # fmt: skip


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("case", sorted(CASES))
def test_route_check_and_submit_refuse_a_project_name_above_a_test_root(
    repo, tmp_path, monkeypatch, capsys, case, route
):
    changes, paths, import_paths = CASES[case]
    changes, paths = ROUTES[route](changes, paths)
    head = _base(repo, changes)
    args = (head, paths, import_paths, ["pkg/tests"], PKG_VERIFY)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == REFUSED


@pytest.mark.parametrize("case", sorted(CASES))
def test_route_check_and_submit_allow_the_same_names_in_a_sealed_package(
    repo, tmp_path, monkeypatch, capsys, case
):
    changes, paths, import_paths = CASES[case]
    head = _base(repo, changes)
    args = (head, paths, import_paths, ["pkg/tests"], PKG_VERIFY)
    answers = _both(repo, tmp_path, monkeypatch, capsys, *args)
    if case == "a case variant":  # past the shadow gate; a later path check refuses the spelling
        assert {error for _code, error in answers} == {"TASK_PATH_PARENT_INVALID"}
    else:
        assert answers == ALLOWED


ORDER = {  # import path src, test root src/x/tests; the checkout root comes first in the ini
    "calc.py": "WHO = 'root'\n",
    "src/calc.py": "WHO = 'src'\n",
    "src/x/__init__.py": "",
    "src/x/tests/test_calc.py": "import calc\n\n\ndef test_who():\n    assert calc.WHO == 'src'\n",
}


@pytest.mark.parametrize(
    ("package_root", "path"),
    [
        (True, "src/__init__.py"),  # pytest then inserts the checkout root, not src, in front
        (False, "src/calc.py"),  # a test commit adding src/x/tests/__init__.py puts src in front
    ],
)
def test_route_check_and_submit_refuse_a_change_of_order_between_project_roots(
    repo, tmp_path, monkeypatch, capsys, package_root, path
):
    files = ORDER | ({"src/x/tests/__init__.py": ""} if package_root else {})
    head = commit_change(repo, files)
    verify = (("pytest", ("python3", "-m", "pytest", "-q", "src/x/tests/test_calc.py")),)
    args = (head, [path], ["src"], ["src/x/tests"], verify)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == REFUSED


def test_route_check_and_submit_refuse_an_init_that_turns_a_namespace_into_a_shadow(
    repo, tmp_path, monkeypatch, capsys
):
    # foo/ has no __init__.py at the base, so `import foo` finds src/foo.py; with one it is foo/.
    head = commit_change(repo, {"foo/data.txt": "x\n", "src/foo.py": "x = 1\n"})
    args = (head, ["foo/__init__.py"], ["src"], ["tests"], VERIFY)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == REFUSED


def test_an_init_in_a_directory_no_other_entry_names_stays_allowed(
    repo, tmp_path, monkeypatch, capsys
):
    head = commit_change(repo, {"foo/data.txt": "x\n", "tests/test_calc.py": "x = 1\n"})
    args = (head, ["foo/__init__.py"], ["src"], ["tests"], VERIFY)
    assert _both(repo, tmp_path, monkeypatch, capsys, *args) == ALLOWED


def _task(repo, paths, roots=("pkg/tests",), import_paths=(), allow_deletions=False):
    head = git(repo, "rev-parse", "HEAD")
    verify = (("pytest", ("python3", "-m", "pytest", "-q", f"{roots[0]}/test_calc.py")),)
    return TaskSpec("key", "card", "t", "fix", repo, head, paths, verify, test_roots=roots,
                    import_paths=import_paths, allow_deletions=allow_deletions)  # fmt: skip


@pytest.mark.parametrize(
    ("path", "allow_deletions", "refused"),
    [
        ("pkg/__init__.py", True, True),  # it may be deleted: pytest would then insert pkg
        ("pkg/__init__.py", False, False),  # only modified: the inserted directory stays put
        ("pkg/sub/__init__.py", False, False),  # not directly above the root
        ("pkg/__INIT__.py", False, True),  # a new file (case variant)
        ("pkg/__init__.pyc", False, True),
    ],
)
def test_an_init_directly_above_a_test_root_may_only_be_modified(
    repo, path, allow_deletions, refused
):
    _base(repo, {"pkg/sub/__init__.py": ""})
    task = _task(repo, (path,), allow_deletions=allow_deletions)
    if refused:
        with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
            check_task(task)
    else:
        assert check_task(task)


def test_a_name_in_another_directory_above_a_test_root_counts(repo):
    commit_change(repo, {"a/tests/test_calc.py": "x = 1\n", "b/tests/test_calc.py": "x = 1\n",
                         "a/calc.py": "x = 1\n", "b/calc/impl.py": "x = 1\n"})  # fmt: skip
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: b/calc/impl.py$"):
        check_task(_task(repo, ("b/calc/impl.py",), roots=("a/tests", "b/tests")))


def test_without_the_base_listing_a_path_above_a_test_root_stays_protected(repo):
    gone = TaskSpec("key", "card", "t", "fix", repo, "d" * 40, ("pkg/calc/impl.py",), PKG_VERIFY,
                    test_roots=("pkg/tests",))  # fmt: skip
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/calc/impl.py$"):
        check_task(gone)  # git cannot list a base that is not in the repo


@pytest.mark.parametrize("path", ["src/pluggy/core.py", "src/pluggy/sub/deep.py"])
def test_an_installed_package_under_an_import_path_stays_editable(repo, path):
    # pluggy is installed in the verifier's Python, as an editable install of the project would
    # be. src is an import path, so src/pluggy already is the pluggy the tests import; only
    # src/pluggy is above the root, and its entries name nothing else.
    files = {"src/pluggy/__init__.py": "", "src/pluggy/tests/__init__.py": ""}
    commit_change(repo, files | {"src/pluggy/tests/test_calc.py": "x = 1\n", path: "x = 1\n"})
    task = _task(repo, (path,), roots=("src/pluggy/tests",), import_paths=("src",))
    assert check_task(task)


@pytest.mark.sandbox
@pytest.mark.parametrize(("delete_init", "passed"), [(True, True), (False, False)])
def test_a_project_named_subpackage_really_shadows_once_pkg_is_inserted(
    repo, tmp_path, delete_init, passed
):
    """Reachability on the verifier's own pytest, not the fix: calc.py stays wrong, yet with
    pkg/__init__.py deleted the test imports pkg/calc/ and passes; kept, it fails."""
    _base(repo, {})
    delete = ("pkg/__init__.py",) if delete_init else ()
    fix = "def half():\n    return 0.5\n"
    head = commit_change(repo, {"pkg/calc/impl.py": fix}, delete=delete)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/test_calc.py"], ("pkg/tests",)
    )
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable), head, (spec,), "r1"
    )
    assert run.observation.passed is passed, (run.why, run.log_path.read_text())


@pytest.mark.sandbox
@pytest.mark.parametrize(("add_init", "passed"), [(True, True), (False, False)])
def test_an_init_really_turns_a_namespace_directory_into_the_module(
    repo, tmp_path, add_init, passed
):
    """Reachability, not the fix: foo/__init__.py makes `import foo` take foo/ over src/foo.py."""
    test = "import foo\n\n\ndef test_who():\n    assert foo.WHO == 'root'\n"
    files = {"foo/data.txt": "x\n", "src/foo.py": "WHO = 'src'\n", "tests/test_calc.py": test}
    head = commit_change(repo, files | ({"foo/__init__.py": "WHO = 'root'\n"} if add_init else {}))
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_calc.py"])
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable, import_paths=("src",)),
        head,
        (spec,),
        "r1",
    )
    assert run.observation.passed is passed, (run.why, run.log_path.read_text())


@pytest.mark.sandbox
@pytest.mark.parametrize(("add_init", "passed"), [(False, True), (True, False)])
def test_an_init_above_the_root_really_changes_which_project_module_wins(
    repo, tmp_path, add_init, passed
):
    """Reachability, not the fix: with src/x/tests a package, pytest puts src in front and the test
    gets src/calc.py; with a new src/__init__.py it puts the checkout root there: calc.py wins."""
    files = ORDER | {"src/x/tests/__init__.py": ""} | ({"src/__init__.py": ""} if add_init else {})
    head = commit_change(repo, files)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "src/x/tests/test_calc.py"], ("src/x/tests",)
    )
    (run,) = run_verifiers(
        VerifyContext(repo, tmp_path / "runs", python=sys.executable, import_paths=("src",)),
        head,
        (spec,),
        "r1",
    )
    assert run.observation.passed is passed, (run.why, run.log_path.read_text())
