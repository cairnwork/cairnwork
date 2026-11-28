"""Direct contract tests for tamper_scan_plugin's parser and Git boundaries."""

import subprocess
import sys

import pytest

import cairnwork.tamper_scan_plugin as plugin
from cairnwork.gitcmd import GitCommandError
from cairnwork.tamper_scan_budget import ScanBudget


@pytest.mark.parametrize(
    "listing",
    [
        None,
        [],
        {"rows": [], "extra": 1},
        {"rows": [["mystery", []]]},
        {"rows": [["ok"]]},
        {"rows": [["ok", "pytest_configure"]]},
        {"rows": [["ok", [1]]]},
        {"rows": [["ok", [[]]]]},
        {"rows": [["ok", ["not_a_hook"]]]},
        {"rows": [["syntax", ["pytest_configure"]]]},
        {"rows": [["fail", ["pytest_configure"]]]},
    ],
)
def test_rows_ok_rejects_malformed_or_untrusted_rows(listing):
    assert not plugin._rows_ok(listing, 1)


def test_rows_ok_accepts_only_known_hooks_on_success_rows():
    six_hooks = sorted(plugin.HOOK_NAMES)[:6]
    assert plugin._rows_ok({"rows": [["ok", six_hooks]]}, 1)
    assert not plugin._rows_ok({"rows": [["ok", six_hooks + ["unknown"]]]}, 1)


@pytest.mark.parametrize("flag", ["-I", "-S", "-B"])
def test_parse_blobs_invokes_interpreter_with_isolation_flags(monkeypatch, flag):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b'{"rows":[["ok",[]]]}', b"")

    monkeypatch.setattr(plugin.subprocess, "run", run)
    assert plugin.parse_blobs([b"x=1"], sys.executable, ScanBudget()) == [("ok", set())]
    assert flag in seen["argv"]
    assert seen["timeout"] > 0
    assert seen["check"] is False
    assert seen["env"] == {} and seen["cwd"] == "/"
    assert isinstance(seen["input"], bytes)


def test_parse_blobs_returns_none_and_marks_budget_on_process_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr(plugin.subprocess, "run", fail)
    budget = ScanBudget()
    assert plugin.parse_blobs([b"x=1"], sys.executable, budget) is None
    assert budget.over


def test_batch_rejects_non_blob_git_objects(repo):
    from conftest import commit_change, git

    commit_change(repo, {"a.py": "alpha\n"})
    commit = git(repo, "rev-parse", "HEAD")
    with pytest.raises(GitCommandError):
        plugin._batch(repo, [commit])


def test_file_rules_rejects_partial_or_invalid_commit_ids(repo):
    with pytest.raises(GitCommandError, match="full commit id"):
        plugin.file_rules(repo, "bad", "0" * 40, ScanBudget(), sys.executable)
    with pytest.raises(GitCommandError, match="full commit id"):
        plugin.file_rules(repo, "0" * 40, "bad", ScanBudget(), sys.executable)


def test_rows_ok_all_known_hooks_are_still_a_valid_set():
    names = sorted(plugin.HOOK_NAMES)
    assert len(names) >= 6
    assert plugin._rows_ok({"rows": [["ok", names]]}, 1)


def test_parse_blobs_timeout_is_bounded_by_remaining_budget(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b'{"rows":[["ok",[]]]}', b"")

    monkeypatch.setattr(plugin.subprocess, "run", run)
    budget = ScanBudget(seconds=0.05)
    assert plugin.parse_blobs([b"x=1"], sys.executable, budget) == [("ok", set())]
    assert 0.001 <= seen["timeout"] < 0.5


def test_parse_blobs_oversized_valid_output_fails_closed(monkeypatch):
    monkeypatch.setattr(plugin, "MAX_OUTPUT", 10)

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, b'{"rows":[["ok",[]]]}' + b" " * 20, b"")

    monkeypatch.setattr(plugin.subprocess, "run", run)
    assert plugin.parse_blobs([b"x=1"], sys.executable, ScanBudget()) is None


def test_parse_blobs_accepts_output_exactly_at_limit(monkeypatch):
    valid = b'{"rows":[["ok",[]]]}'
    monkeypatch.setattr(plugin, "MAX_OUTPUT", len(valid))

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, valid, b"")

    monkeypatch.setattr(plugin.subprocess, "run", run)
    assert plugin.parse_blobs([b"x=1"], sys.executable, ScanBudget()) == [("ok", set())]


def test_parse_blobs_timeout_error_deducts_elapsed_time_once(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("unavailable")

    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(plugin.subprocess, "run", fail)
    monkeypatch.setattr(plugin.time, "monotonic", lambda: next(ticks))
    budget = ScanBudget(seconds=10)
    assert plugin.parse_blobs([b"x=1"], sys.executable, budget) is None
    assert budget.over
    assert budget.seconds_left == 9.75
