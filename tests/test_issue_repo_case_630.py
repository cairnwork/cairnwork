"""Repo slug casing must not split Issue intake identity (#630)."""

import pytest
from test_issue_runner import issue
from test_issue_runner import setup as setup

import cairnwork.issue_runner as runner
from cairnwork.issue_github import GitHub


class PayloadGitHub(GitHub):
    def __init__(self, repo):
        super().__init__("/bin/false", repo)
        self.issue = issue()

    def trust(self, number, label):
        return ""

    def current(self, number):
        return {key: self.issue[key] for key in ("title", "body")}

    def relabel(self, number, add, remove):
        pass

    def comment(self, number, text):
        pass


def test_repo_case_uses_one_key_hash_and_record_name(setup, monkeypatch):
    captured = []
    monkeypatch.setattr(runner, "_bound", lambda *args: None)
    monkeypatch.setattr(runner, "_remember", lambda *args: None)

    def submit(_intake, payload):
        captured.append(payload)
        return {"task_id": "task", "duplicate": False}

    monkeypatch.setattr(runner.Intake, "submit", submit)

    repositories = ["James3014/demo", "james3014/DEMO"]
    paths = []
    for repo in repositories:
        github = PayloadGitHub(repo)
        runner._take_one(setup["intake"], github, setup["clone"], "base", github.issue)
        paths.append(runner._record_path(setup["intake"], github.repo, 7).name)

    assert captured[0]["idempotency_key"] == captured[1]["idempotency_key"]
    assert captured[0]["card_hash"] == captured[1]["card_hash"]
    assert captured[0]["instructions"] == captured[1]["instructions"]
    assert paths[0] == paths[1]


def test_repo_slug_validation_still_rejects_invalid_input():
    with pytest.raises(ValueError, match="repo must be OWNER/NAME"):
        GitHub("/bin/false", "owner name/repo")
