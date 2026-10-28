"""#351 (follow-up of #300): the base config's pytest ``pythonpath`` is an import path too.

run_verifiers puts the base commit's pytest ``pythonpath`` on the verifier's import path
(pytest_config.base_settings), next to the task's ``import_paths``. The shadow gate
(verifier_spec.protected_verifier_inputs) must reason about the same directories, so with a base
``pythonpath = src`` and no task import_paths a candidate ``src/json.py`` is still protected.
``cairnwork-route check`` must also read the state config's import_paths, as submit does.

#360: pytest itself puts directories on sys.path too. In its default (prepend) import mode a test
module in a package test root puts the directory above its top package there, so with test root
``pkg/tests`` and ``pkg/tests/__init__.py`` a candidate ``pkg/fractions.py`` replaces the standard
library module for the verifier's tests. Every directory above a test root is an import root of
the gate.
"""

import io
import json
import sys
from typing import Any

import pytest
from conftest import commit_change, git

from cairnwork import route
from cairnwork.blackbox_exec import run_blackbox
from cairnwork.gitcmd import GitCommandError
from cairnwork.import_path_tree import ImportPathError
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task
from cairnwork.verifiers import parse_verifier, run_verifiers

VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)
CONFIGS = {
    "pytest.ini": "[pytest]\npythonpath = src\n",
    "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    "tox.ini": "[pytest]\npythonpath = ./src\n",  # normalized like an import path (#348)
    "setup.cfg": "[tool:pytest]\npythonpath = src\n",
}


def task(repo, path, import_paths=()):
    head = git(repo, "rev-parse", "HEAD")
    return TaskSpec(
        "key", "card", "title", "fix", repo, head, (path,), VERIFY, import_paths=import_paths
    )


@pytest.mark.parametrize("config", sorted(CONFIGS))
@pytest.mark.parametrize("path", ["src/json.py", "src/json/__init__.py", "src/brand_new.py"])
def test_a_shadow_under_the_base_pythonpath_is_refused(repo, config, path):
    commit_change(repo, {config: CONFIGS[config]})
    if path != "src/brand_new.py":
        commit_change(repo, {path: "x = 1\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, path))


@pytest.mark.parametrize("path", ["src/calc.py", "src/calcpkg/core.py", "src/calcpkg/__init__.py"])
def test_honest_files_under_the_base_pythonpath_stay_allowed(repo, path):
    commit_change(repo, {"pytest.ini": CONFIGS["pytest.ini"], path: "x = 1\n"})
    assert check_task(task(repo, path))


@pytest.mark.parametrize("pythonpath", ["", "pythonpath = .\n", "pythonpath = ./\n"])
def test_without_a_src_pythonpath_src_files_are_not_import_roots(repo, pythonpath):
    commit_change(repo, {"pytest.ini": "[pytest]\n" + pythonpath, "src/json.py": "x = 1\n"})
    assert check_task(task(repo, "src/json.py"))


def test_the_base_pythonpath_counts_next_to_task_import_paths(repo):
    commit_change(repo, {"pytest.ini": CONFIGS["pytest.ini"], "src/json.py": "x = 1\n"})
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(task(repo, "src/json.py", import_paths=("other",)))


def test_the_gate_reads_the_config_of_the_base_not_of_a_later_commit(repo):
    base = commit_change(repo, {"pytest.ini": CONFIGS["pytest.ini"], "src/json.py": "x = 1\n"})
    commit_change(repo, delete=("pytest.ini",))  # HEAD has no pythonpath; the base does
    later = TaskSpec("key", "card", "t", "fix", repo, base, ("src/json.py",), VERIFY)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(later)


def test_run_verifiers_keeps_the_base_pythonpath_next_to_import_paths(repo, tmp_path, monkeypatch):
    head = commit_change(repo, {"pytest.ini": CONFIGS["pytest.ini"]})
    seen = _stop_at_plain_dirs(monkeypatch, "cairnwork.verifiers")
    spec = parse_verifier("v", ["python3", "-m", "pytest", "tests/test_calc.py"])
    with pytest.raises(ImportPathError):
        run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable,
                      base_commit=head, import_paths=("other",))  # fmt: skip
    assert seen == [("other", "src")]


def test_the_blackbox_run_keeps_the_base_pythonpath_too(repo, tmp_path, monkeypatch):
    head = commit_change(repo, {"pytest.ini": CONFIGS["pytest.ini"]})
    seen = _stop_at_plain_dirs(monkeypatch, "cairnwork.blackbox_exec")
    declaration: Any = None  # never read: the run stops at the import-path check
    with pytest.raises(ImportPathError):
        run_blackbox(declaration, repo, head, tmp_path / "runs", "r1", python=sys.executable,
                     base_commit=head, import_paths=("other",))  # fmt: skip
    assert seen == [("other", "src")]


def _stop_at_plain_dirs(monkeypatch, module: str) -> list:
    """Record the import paths a run checks, then stop it before anything runs."""
    seen = []

    def stop(_repo, _commit, paths):
        seen.append(tuple(paths))
        raise ImportPathError("stop before running")

    monkeypatch.setattr(f"{module}.require_plain_dirs", stop)
    return seen


def test_a_malformed_base_config_is_refused_before_binding(repo):
    commit_change(repo, {"pytest.ini": "[pytest\npythonpath = src\n", "src/calc.py": "x = 1\n"})
    with pytest.raises(TaskRejected, match="PYTEST_CONFIG_ERROR"):
        check_task(task(repo, "src/calc.py"))


def test_a_base_missing_from_the_repo_is_left_to_the_later_refusals(repo):
    # No base here: nothing can run against it (intake refuses TASK_PATH_UNREADABLE), so the gate
    # does not crash on reading its config.
    gone = TaskSpec("key", "card", "t", "fix", repo, "d" * 40, ("src/calc.py",), VERIFY)
    assert check_task(gone)


def test_a_repository_git_cannot_read_fails_closed(tmp_path):
    unreadable = TaskSpec("key", "card", "t", "fix", tmp_path, "d" * 40, ("src/a.py",), VERIFY)
    with pytest.raises(TaskRejected, match="PYTEST_CONFIG_ERROR"):
        check_task(unreadable)  # not a git repository: not "the base is absent"


def test_an_unreadable_config_at_a_present_base_fails_closed(repo, monkeypatch):
    def unreadable(*_args):
        raise GitCommandError("cat-file failed")

    commit_change(repo, {"src/calc.py": "x = 1\n"})
    monkeypatch.setattr("cairnwork.pytest_config.verifier_settings", unreadable)
    with pytest.raises(TaskRejected, match="PYTEST_CONFIG_ERROR"):
        check_task(task(repo, "src/calc.py"))


def _route(state, command, payload, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = route.main(["--state", str(state), command])
    return code, json.loads(capsys.readouterr().out)


def _state(tmp_path, monkeypatch, capsys, *, accepting=True, **extra):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = {"verifier_account": False, "import_paths": ["src"]} | extra
    (state / "config.json").write_text(json.dumps(config))
    if not accepting:
        assert route.main(["--state", str(state), "drain"]) == 0
        capsys.readouterr()
    return state


def _payload(repo, head, path):
    return {
        "idempotency_key": "gw-351",
        "card_hash": "card-351",
        "title": "t",
        "instructions": "i",
        "repo": str(repo),
        "base_commit": head,
        "allowed_files": [path],
        "verifiers": [["pytest", list(VERIFY[0][1])]],
    }


def test_route_check_reads_the_state_import_paths_like_submit(repo, tmp_path, monkeypatch, capsys):
    head = commit_change(repo, {"src/json.py": "x = 1\n", "tests/test_calc.py": "x = 1\n"})
    state = _state(tmp_path, monkeypatch, capsys)
    payload = _payload(repo, head, "src/json.py")
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    assert [(code, out.get("error")) for code, out in answers] == [
        (2, "PROTECTED_VERIFIER_INPUT"),
        (2, "PROTECTED_VERIFIER_INPUT"),
    ]


def test_route_check_and_submit_both_pass_an_honest_file(repo, tmp_path, monkeypatch, capsys):
    head = commit_change(repo, {"src/calc.py": "x = 1\n", "tests/test_calc.py": "x = 1\n"})
    state = _state(tmp_path, monkeypatch, capsys, accepting=False)  # drained: submit binds nothing
    payload = _payload(repo, head, "src/calc.py")
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    assert [(code, out.get("error")) for code, out in answers] == [
        (0, None),
        (2, "NOT_ACCEPTING"),  # past every gate; refused only because intake is drained
    ]


# #360: the directories above a test root (pytest's prepend import mode).
PKG_VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "pkg/tests/test_calc.py")),)
# Which directory above the root pytest inserts depends on files the gate cannot pin: a test commit
# may add pkg/tests/__init__.py, a candidate may add or delete __init__.py outside the root, and a
# conftest.py above the root is imported the same way.
LAYOUTS = {
    "package root": {"pkg/tests/__init__.py": ""},
    "package root in a package": {"pkg/__init__.py": "", "pkg/tests/__init__.py": ""},
    "plain root, conftest above": {"pkg/conftest.py": ""},
    "plain root": {},
}


def pkg_task(repo, paths, roots=("pkg/tests",)):
    head = git(repo, "rev-parse", "HEAD")
    return TaskSpec("key", "card", "t", "fix", repo, head, paths, PKG_VERIFY, test_roots=roots)


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
@pytest.mark.parametrize("path", ["pkg/fractions.py", "pkg/json/__init__.py", "pkg/brand_new.py"])
def test_a_shadow_above_a_test_root_is_refused(repo, layout, path):
    files = {**LAYOUTS[layout], "pkg/tests/test_calc.py": "x = 1\n"}
    if path != "pkg/brand_new.py":
        files[path] = "x = 1\n"
    commit_change(repo, files)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(pkg_task(repo, (path,)))


def test_every_directory_above_a_nested_test_root_is_an_import_root(repo):
    # a/b/tests/__init__.py puts a/b on sys.path; a candidate adding a/b/__init__.py moves it to a.
    roots = ("a/b/tests",)
    commit_change(repo, {"a/b/tests/__init__.py": "", "a/fractions.py": "x = 1\n"})
    for path in ("a/b/fractions.py", "a/fractions.py"):
        with pytest.raises(TaskRejected, match=f"PROTECTED_VERIFIER_INPUT: {path}$"):
            check_task(pkg_task(repo, (path,), roots))


@pytest.mark.parametrize("path", ["pkg/calc.py", "pkg/calcpkg/__init__.py", "pkg/sub/json.py"])
def test_honest_files_above_a_package_test_root_stay_allowed(repo, path):
    commit_change(repo, {"pkg/tests/__init__.py": "", path: "x = 1\n"})
    assert check_task(pkg_task(repo, (path,)))


def test_a_flat_test_root_adds_no_import_root(repo):
    commit_change(repo, {"tests/__init__.py": "", "pkg/fractions.py": "x = 1\n"})
    assert check_task(task(repo, "pkg/fractions.py"))


@pytest.mark.parametrize(("path", "expected"), [("pkg/fractions.py", None), ("pkg/calc.py", 0)])
def test_route_check_and_submit_guard_the_root_above_a_test_root(
    repo, tmp_path, monkeypatch, capsys, path, expected
):
    files = {"pkg/tests/__init__.py": "", "pkg/tests/test_calc.py": "x = 1\n", path: "x = 1\n"}
    head = commit_change(repo, files)
    state = _state(tmp_path, monkeypatch, capsys, accepting=False, test_roots=["pkg/tests"])
    payload = _payload(repo, head, path) | {"verifiers": [["pytest", list(PKG_VERIFY[0][1])]]}
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    if expected is None:  # refused by the shadow gate on both paths, before binding
        assert [(code, out.get("error")) for code, out in answers] == [
            (2, "PROTECTED_VERIFIER_INPUT"),
            (2, "PROTECTED_VERIFIER_INPUT"),
        ]
    else:  # an honest file passes every gate; submit stops only because intake is drained
        assert [(code, out.get("error")) for code, out in answers] == [
            (0, None),
            (2, "NOT_ACCEPTING"),
        ]


@pytest.mark.sandbox
def test_the_verifiers_pytest_really_imports_from_above_a_package_test_root(repo, tmp_path):
    """Reachability on the verifier's own pytest, not the fix: in the package test root pkg/tests,
    pkg/fractions.py replaces the standard library module a protected test compares against, and
    the wrong answer passes."""
    files = {
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_calc.py": (
            "import fractions\n\n\ndef test_half():\n    assert fractions.Fraction(1, 2) == 7\n"
        ),
        "pkg/fractions.py": (
            "class Fraction:\n    def __init__(self, *args):\n        pass\n\n"
            "    def __eq__(self, other):\n        return True\n"
        ),
    }
    head = commit_change(repo, files)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/test_calc.py"], ("pkg/tests",)
    )
    (run,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed, (run.why, run.log_path.read_text())


# PR #376 round 1 (V1): a package above the root named like a module, at any depth.
SUBPACKAGE = {
    "pkg/tests/test_calc.py": "x = 1\n",
    "pkg/fractions/__init__.py": "from .impl import *\n",
    "pkg/fractions/impl.py": "x = 1\n",
    "pkg/calc/impl.py": "x = 1\n",
}
DEEP = {  # base layout added to SUBPACKAGE, the candidate's paths
    "pkg/__init__.py deleted": ({"pkg/__init__.py": "", "pkg/tests/__init__.py": ""},
                                ["pkg/__init__.py", "pkg/fractions/impl.py"]),
    "the test commit adds pkg/tests/__init__.py": ({}, ["pkg/fractions/impl.py"]),
    "deeper in the package": ({"pkg/tests/__init__.py": ""}, ["pkg/fractions/sub/deep.py"]),
    "an installed module's name": ({"pkg/tests/__init__.py": ""}, ["pkg/pluggy/impl.py"]),
    "a case variant (APFS)": ({"pkg/tests/__init__.py": ""}, ["pkg/Fractions/impl.py"]),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(DEEP))
def test_route_check_and_submit_refuse_a_shadowing_package_above_a_test_root(
    repo, tmp_path, monkeypatch, capsys, case
):
    layout, paths = DEEP[case]
    head = commit_change(repo, {**SUBPACKAGE, **layout})
    state = _state(tmp_path, monkeypatch, capsys, accepting=False, test_roots=["pkg/tests"])
    payload = _payload(repo, head, paths[0]) | {
        "allowed_files": paths,
        "verifiers": [["pytest", list(PKG_VERIFY[0][1])]],
    }
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    assert [(code, out.get("error")) for code, out in answers] == [
        (2, "PROTECTED_VERIFIER_INPUT"),
        (2, "PROTECTED_VERIFIER_INPUT"),
    ]


@pytest.mark.parametrize(
    ("roots", "import_paths", "path"),
    [
        (("pkg/tests",), (), "pkg/calc/impl.py"),  # a package whose name shadows nothing
        (("tests",), (), "pkg/fractions/impl.py"),  # a flat root: nothing above it
        (("tests",), ("src",), "src/json/decoder.py"),  # under an import path it is the module
    ],
)
def test_honest_files_in_packages_stay_allowed(repo, roots, import_paths, path):
    commit_change(repo, {**SUBPACKAGE, "pkg/tests/__init__.py": "", path: "x = 1\n"})
    head = git(repo, "rev-parse", "HEAD")
    verify = PKG_VERIFY if roots == ("pkg/tests",) else VERIFY
    assert check_task(TaskSpec("key", "card", "t", "fix", repo, head, (path,), verify,
                               test_roots=roots, import_paths=import_paths))  # fmt: skip


def test_without_the_module_inventory_a_package_above_the_root_stays_protected(repo, monkeypatch):
    def unreadable(_python):
        raise OSError("no interpreter")

    commit_change(repo, {**SUBPACKAGE, "pkg/tests/__init__.py": ""})
    monkeypatch.setattr("cairnwork.verifier_spec._importable_names", unreadable)
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT: pkg/calc/impl.py$"):
        check_task(pkg_task(repo, ("pkg/calc/impl.py",)))


def test_a_base_missing_from_the_repo_still_guards_the_directories_above_a_root(repo):
    # V2: they come from the test roots alone, so the gate needs no base for them.
    gone = TaskSpec("key", "card", "t", "fix", repo, "d" * 40, ("pkg/fractions.py",), PKG_VERIFY,
                    test_roots=("pkg/tests",))  # fmt: skip
    with pytest.raises(TaskRejected, match="PROTECTED_VERIFIER_INPUT"):
        check_task(gone)


@pytest.mark.sandbox
@pytest.mark.parametrize(("delete_init", "passed"), [(True, True), (False, False)])
def test_deleting_the_init_above_the_root_really_lets_a_subpackage_shadow(
    repo, tmp_path, delete_init, passed
):
    """Reachability on the verifier's own pytest, not the fix: with pkg/__init__.py deleted,
    pkg/fractions/ replaces the standard library module and the wrong answer passes; kept, the
    test fails as it should."""
    test = "import fractions\n\n\ndef test_half():\n    assert fractions.Fraction(1, 2) == 7\n"
    inits = {"pkg/__init__.py": "", "pkg/tests/__init__.py": ""}
    commit_change(repo, {**SUBPACKAGE, **inits, "pkg/tests/test_calc.py": test})
    fake = "class Fraction:\n    def __init__(self, *args):\n        pass\n\n"
    fake += "    def __eq__(self, other):\n        return True\n"
    delete = ("pkg/__init__.py",) if delete_init else ()
    head = commit_change(repo, {"pkg/fractions/impl.py": fake}, delete=delete)
    spec = parse_verifier(
        "v", ["python3", "-m", "pytest", "pkg/tests/test_calc.py"], ("pkg/tests",)
    )
    (run,) = run_verifiers(repo, head, (spec,), tmp_path / "runs", "r1", python=sys.executable)
    assert run.observation.passed is passed, (run.why, run.log_path.read_text())
