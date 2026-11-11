"""#1177: Cairnwork environment failures must leave queued Issues queued."""

import json
import subprocess
import sys

import pytest
from test_issue_runner import BLOCK, FakeGitHub, issue  # noqa: F401

import cairnwork.issue_runner as runner
import cairnwork.namespace_shadow as namespace_shadow
import cairnwork.pytest_config as pytest_config
from cairnwork.gitcmd import GitCommandError

pytest_plugins = ("test_issue_runner",)


def _namespace_issue():
    block = {
        **BLOCK,
        "allowed_files": ["src/pkg/new.py"],
    }
    return issue(block=block)


def _prepare_namespace(setup):
    config = setup["intake"].state / "config.json"
    config.write_text(json.dumps({"import_paths": ["src"]}))
    (setup["clone"] / "src/pkg").mkdir(parents=True)
    (setup["clone"] / "src/pkg/base.py").write_text("base = True\n")
    subprocess.run(["git", "-C", str(setup["clone"]), "add", "src/pkg/base.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(setup["clone"]),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-m",
            "add pkg",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(setup["clone"]), "push", "origin", "HEAD:main"],
        check=True,
        capture_output=True,
    )


def _assert_environment_error(result, github):
    assert "error" in result
    assert "refused" not in result
    assert github.labels[7] == {runner.QUEUE}
    assert github.comments == []


@pytest.mark.parametrize(
    "cause",
    [FileNotFoundError("git missing"), subprocess.TimeoutExpired("git", 1)],
)
def test_pytest_config_environment_failure_leaves_issue_queued(setup, monkeypatch, cause):
    github = FakeGitHub([issue()])
    original = pytest_config.run_git

    def run_git(repo, *args, **kwargs):
        if args and args[0] == "rev-parse":
            raise GitCommandError("rev-parse could not run") from cause
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(pytest_config, "run_git", run_git)
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_environment_error(result, github)


def test_pytest_config_git_refusal_still_refuses(setup, monkeypatch):
    github = FakeGitHub([issue()])

    def run_git(_repo, *args, **_kwargs):
        if args and args[0] == "rev-parse":
            error = subprocess.CalledProcessError(128, "git", stderr="repository refused")
            raise GitCommandError("rev-parse failed") from error
        raise AssertionError(args)

    monkeypatch.setattr(pytest_config, "run_git", run_git)
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "PYTEST_CONFIG_ERROR" in result["refused"]
    assert github.labels[7] == {runner.NEEDS_FIX}


@pytest.mark.parametrize(
    "cause",
    [FileNotFoundError("git missing"), subprocess.TimeoutExpired("git", 1)],
)
def test_namespace_listing_environment_failure_leaves_issue_queued(setup, monkeypatch, cause):
    _prepare_namespace(setup)
    github = FakeGitHub([_namespace_issue()])
    original = namespace_shadow.run_git

    def run_git(repo, *args, **kwargs):
        if args and args[0] == "ls-tree":
            raise GitCommandError("ls-tree could not run") from cause
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(namespace_shadow, "run_git", run_git)
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_environment_error(result, github)


def test_namespace_probe_timeout_leaves_issue_queued(setup, monkeypatch):
    _prepare_namespace(setup)
    github = FakeGitHub([_namespace_issue()])
    monkeypatch.setattr(
        namespace_shadow,
        "_run_probe",
        lambda *_args: (_ for _ in ()).throw(subprocess.TimeoutExpired("python", 1)),
    )
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_environment_error(result, github)


def test_namespace_git_refusal_still_refuses(setup, monkeypatch):
    _prepare_namespace(setup)
    github = FakeGitHub([_namespace_issue()])
    original = namespace_shadow.run_git

    def run_git(repo, *args, **kwargs):
        if args and args[0] == "ls-tree":
            error = subprocess.CalledProcessError(128, "git", stderr="tree refused")
            raise GitCommandError("ls-tree failed") from error
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(namespace_shadow, "run_git", run_git)
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    assert "PROTECTED_VERIFIER_INPUT" in result["refused"]
    assert github.labels[7] == {runner.NEEDS_FIX}


def test_absent_base_check_cannot_start_after_tree_refusal_leaves_issue_queued(setup, monkeypatch):
    _prepare_namespace(setup)
    github = FakeGitHub([_namespace_issue()])

    def run_git(_repo, *args, **_kwargs):
        if args and args[0] == "ls-tree":
            error = subprocess.CalledProcessError(128, "git", stderr="tree refused")
            raise GitCommandError("ls-tree failed") from error
        if args and args[0] == "rev-parse":
            raise GitCommandError("rev-parse could not start") from FileNotFoundError("git missing")
        raise AssertionError(args)

    monkeypatch.setattr(namespace_shadow, "run_git", run_git)
    result = runner.take(setup["intake"], github, setup["clone"])[0]
    _assert_environment_error(result, github)


def test_a_non_utf8_base_path_is_protected(repo):
    def plumbing(*args, input=None):
        return subprocess.run(
            ["git", "-C", str(repo), *args], input=input, capture_output=True, check=True
        ).stdout.strip()

    blob = plumbing("hash-object", "-w", "--stdin", input=b"value = 1\n")
    subtree = plumbing("mktree", "-z", input=b"100644 blob " + blob + b"\tbad-\xff.py\0")
    source = plumbing("mktree", "-z", input=b"040000 tree " + subtree + b"\tpkg\0")
    root = plumbing("mktree", "-z", input=b"040000 tree " + source + b"\tsrc\0")
    commit = plumbing(
        "-c", "user.name=t", "-c", "user.email=t@e", "commit-tree", root, input=b"base\n"
    )
    plumbing("update-ref", "refs/heads/main", commit)
    assert namespace_shadow.namespace_shadows(
        ("src/pkg/new.py",),
        ("src",),
        ("tests",),
        frozenset(),
        repo=repo,
        base_commit=commit,
        python=sys.executable,
        allow_deletions=False,
    ) == {"src/pkg/new.py"}
