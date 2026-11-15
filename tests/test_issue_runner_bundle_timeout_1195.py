"""Issue #1195: a timeout reading the stored (Cairnwork-built) bundle stays an environment error."""

import json
import subprocess

from test_deferred_store import make_bundle, used_dir
from test_issue_runner import BLOCK, FakeGitHub, issue
from test_issue_runner import setup as setup  # noqa: F401

import cairnwork.deferred_store as deferred_store
import cairnwork.issue_runner as runner


def test_stored_bundle_timeout_in_take_stays_an_environment_error(setup, tmp_path, monkeypatch):
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

    results = [runner.take(intake, github, clone) for _ in range(2)]

    for out in results:  # both passes: an environment error, left queued, never refused
        assert "could not run git" in out[0]["error"]
        assert "refused" not in out[0]
    assert github.labels.get(7, set()) != {runner.NEEDS_FIX}


def test_stored_bundle_git_failure_in_take_stays_queued(setup, tmp_path, monkeypatch):
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

    def failed_stored_fetch(repo, *args, **kwargs):
        if args and args[0] == "fetch" and str(store.bundle(test_commit)) in args:
            failure = subprocess.CalledProcessError(128, ["git", "fetch"], stderr="disk error")
            raise deferred_store.GitCommandError("git fetch failed: disk error") from failure
        return original_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(deferred_store, "run_git", failed_stored_fetch)
    github = FakeGitHub([issue(block={**BLOCK, "test_commit": test_commit})])

    result = runner.take(intake, github, clone)[0]

    assert "could not run git" in result["error"]
    assert "refused" not in result
    assert github.labels[7] == {runner.QUEUE}
