"""Issue #1195: a timed out private test bundle is refused once as an Issue error."""

import json
import subprocess

from test_deferred_store import make_bundle, used_dir
from test_issue_runner import BLOCK, FakeGitHub, issue
from test_issue_runner import setup as setup  # noqa: F401

import cairnwork.deferred_store as deferred_store
import cairnwork.issue_runner as runner


def test_bundle_timeout_is_rejected_once_without_environment_retry(setup, tmp_path, monkeypatch):
    intake, clone = setup["intake"], setup["clone"]
    config_path = intake.state / "config.json"
    config_path.write_text(json.dumps({**intake.config(), "test_source": "bundle"}))
    _base, test_commit, bundle = make_bundle(
        clone, {"tests/test_regression.py": "def test_regression():\n    assert True\n"}, tmp_path
    )
    store = intake.test_store()
    store.path.mkdir(parents=True, mode=0o700, exist_ok=True)
    store.import_bundle(clone, bundle, used_dir(store))
    original_run_git = deferred_store.run_git

    def timeout_bundle_fetch(repo, *args, **kwargs):
        if args and args[0] == "fetch" and str(store.bundle(test_commit)) in args:
            timeout = subprocess.TimeoutExpired(["git", "fetch"], 60)
            raise deferred_store.GitCommandError("git fetch failed: timeout") from timeout
        return original_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", timeout_bundle_fetch)
    github = FakeGitHub([issue(block={**BLOCK, "test_commit": test_commit})])

    first = runner.take(intake, github, clone)
    second = runner.take(intake, github, clone)

    assert "refused" in first[0] and "TASK_TEST_COMMIT_UNREADABLE" in first[0]["refused"]
    assert github.labels[7] == {runner.NEEDS_FIX}
    assert second == []
