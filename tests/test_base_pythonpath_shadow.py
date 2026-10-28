"""#351 (follow-up of #300): the base config's pytest ``pythonpath`` is an import path too.

run_verifiers puts the base commit's pytest ``pythonpath`` on the verifier's import path
(pytest_config.base_settings), next to the task's ``import_paths``. The shadow gate
(verifier_spec.protected_verifier_inputs) must reason about the same directories, so with a base
``pythonpath = src`` and no task import_paths a candidate ``src/json.py`` is still protected.
``cairnwork-route check`` must also read the state config's import_paths, as submit does.
"""

import io
import json

import pytest
from conftest import commit_change, git

from cairnwork import route
from cairnwork.gitcmd import GitCommandError
from cairnwork.pipeline import TaskRejected, TaskSpec, check_task

VERIFY = (("pytest", ("python3", "-m", "pytest", "-q", "tests/test_calc.py")),)
CONFIGS = {
    "pytest.ini": "[pytest]\npythonpath = src\n",
    "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    "tox.ini": "[pytest]\npythonpath = ./src\n",  # normalized like an import path (#348)
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


def test_a_malformed_base_config_is_refused_before_binding(repo):
    commit_change(repo, {"pytest.ini": "[pytest\npythonpath = src\n", "src/calc.py": "x = 1\n"})
    with pytest.raises(TaskRejected, match="PYTEST_CONFIG_ERROR"):
        check_task(task(repo, "src/calc.py"))


def test_a_base_missing_from_the_repo_is_left_to_the_later_refusals(repo):
    # No base here: nothing can run against it (intake refuses TASK_PATH_UNREADABLE), so the gate
    # does not crash on reading its config.
    gone = TaskSpec("key", "card", "t", "fix", repo, "d" * 40, ("src/calc.py",), VERIFY)
    assert check_task(gone)


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


def test_route_check_reads_the_state_import_paths_like_submit(repo, tmp_path, monkeypatch, capsys):
    head = commit_change(repo, {"src/json.py": "x = 1\n", "tests/test_calc.py": "x = 1\n"})
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    config = {"verifier_account": False, "import_paths": ["src"]}
    (state / "config.json").write_text(json.dumps(config))
    payload = {
        "idempotency_key": "gw-351",
        "card_hash": "card-351",
        "title": "t",
        "instructions": "i",
        "repo": str(repo),
        "base_commit": head,
        "allowed_files": ["src/json.py"],
        "verifiers": [["pytest", list(VERIFY[0][1])]],
    }
    answers = [_route(state, cmd, payload, monkeypatch, capsys) for cmd in ("check", "submit")]
    assert [(code, out.get("error")) for code, out in answers] == [
        (2, "PROTECTED_VERIFIER_INPUT"),
        (2, "PROTECTED_VERIFIER_INPUT"),
    ]
