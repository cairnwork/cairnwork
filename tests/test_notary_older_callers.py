"""Issue #343: callers refuse an unmigrated notary before binding work, and report it per Issue."""
# ruff: noqa: F811

import json
import subprocess
import sys

from conftest import git
from test_issue_runner import FakeGitHub, issue, setup  # noqa: F401
from test_notary_migrate import digest, old_notary

import cairnwork.issue_runner as runner
from cairnwork.intake import Intake


def route(state, *args, stdin=None):
    done = subprocess.run(
        [sys.executable, "-I", "-m", "cairnwork.route", "--state", str(state), *args],
        input=json.dumps(stdin) if stdin is not None else "",
        capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    return done.returncode, json.loads(done.stdout)


def request(project):
    return {
        "idempotency_key": "gw-343",
        "card_hash": "card-gw-343",
        "title": "fix add",
        "instructions": "add() is off by one",
        "repo": str(project),
        "base_commit": git(project, "rev-parse", "HEAD"),
        "allowed_files": ["src/calc.py"],
        "verifiers": [["pytest", ["python3", "-m", "pytest", "-q", "tests/test_calc.py"]]],
    }


def test_route_submit_and_check_refuse_before_binding(project, state):
    old_notary(state / "notary.sqlite", 2)
    before = digest(state / "notary.sqlite")
    for command in ("check", "submit"):
        code, out = route(state, command, stdin=request(project))
        assert code == 2 and out["error"] == "NOTARY_SCHEMA_OLDER", (command, out)
        assert "migrate-notary" in out["detail"]
    code, flight = route(state, "in-flight")
    assert code == 0 and flight["count"] == 0  # nothing bound: the deployer's gate stays open
    assert digest(state / "notary.sqlite") == before and not (state / "requests").exists()


def test_take_leaves_the_issue_queued_on_an_unmigrated_state(setup):
    old_notary(setup["intake"].state / "notary.sqlite", 2)
    github = FakeGitHub([issue()])
    out = runner.take(setup["intake"], github, setup["clone"])
    assert out == [{"issue": 7, "skipped": "NOTARY_SCHEMA_OLDER"}]
    assert github.labels[7] == {runner.QUEUE} and github.comments == []
    assert setup["started"] == [] and setup["intake"].in_flight()["count"] == 0


def test_run_once_reports_an_unmigrated_notary_for_the_issue(setup, monkeypatch, capsys):
    github = FakeGitHub([issue()])
    runner.take(setup["intake"], github, setup["clone"])  # taken on a current notary
    path = setup["intake"].state / "notary.sqlite"
    path.unlink(missing_ok=True)
    old_notary(path, 2)  # e.g. the state restored from a backup taken before an upgrade
    # status opens the notary once the task has a candidate; here it opens it at once
    monkeypatch.setattr(Intake, "status", lambda self, **kw: {"status": self.notary})
    monkeypatch.setattr(runner, "GitHub", lambda gh, repo: github)
    argv = ["--state", str(setup["intake"].state), "--repo", "James3014/demo"]
    code = runner.main([*argv, "--clone", str(setup["clone"]), "run-once"])
    out = json.loads(capsys.readouterr().out)
    [reported] = out["settle"]
    assert code == 1 and set(reported) == {"issue", "error"} and reported["issue"] == 7
    assert "NOTARY_SCHEMA_OLDER" in reported["error"]
    assert github.labels[7] == {runner.RUNNING}  # left for the next pass, after migrate-notary
