"""Mutation survivors for the deferred pytest collection gate."""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cairnwork import deferred_collected
from cairnwork.collect_reader import Collected
from cairnwork.gitcmd import GitCommandError
from cairnwork.verifier_spec import VerifierSpec


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True
    )
    (repo / "tests").mkdir()
    (repo / "tests/test_old.py").write_text("def test_old(): pass\n")
    (repo / "src.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, commit


def test_exists_uses_base_tree_and_returns_both_results(tmp_path):
    repo, commit = _repo(tmp_path)
    task = SimpleNamespace(repo=repo, base_commit=commit)
    assert deferred_collected._exists(task, "tests/test_old.py") is True
    assert deferred_collected._exists(task, "tests/new.py") is False


def test_exists_maps_git_command_error_to_false(monkeypatch):
    def fail(*args: Any) -> str:
        raise GitCommandError("missing")

    monkeypatch.setattr(deferred_collected, "run_git", fail)
    assert not deferred_collected._exists(SimpleNamespace(repo="r", base_commit="b"), "x.py")


def test_exists_argv_is_exact(monkeypatch):
    calls = []
    monkeypatch.setattr(deferred_collected, "run_git", lambda *args: calls.append(args) or "")
    task = SimpleNamespace(repo="repo", base_commit="base")
    assert deferred_collected._exists(task, "tests/a.py")
    assert calls == [("repo", "cat-file", "-e", "base:tests/a.py")]


def test_probe_listing_argv_filter_and_limit(monkeypatch):
    calls = []
    listing = "tests/test_data.txt\ntests/helper.py\ntests/test_a.py\nother/test_b.py\n"
    monkeypatch.setattr(deferred_collected, "run_git", lambda *args: calls.append(args) or listing)
    task = SimpleNamespace(repo="repo", base_commit="base")
    assert deferred_collected._probe_files(task, ()) == ("tests/test_a.py",)
    assert calls == [("repo", "ls-tree", "-r", "--name-only", "base")]
    assert deferred_collected._probe_files(task, ("src/test_existing.py",)) == (
        "src/test_existing.py",
    )


def test_probe_git_error_returns_empty(monkeypatch):
    def fail(*args: Any) -> str:
        raise GitCommandError("no tree")

    monkeypatch.setattr(deferred_collected, "run_git", fail)
    assert deferred_collected._probe_files(SimpleNamespace(repo="r", base_commit="b"), ()) == ()


def test_check_with_base_delegates_in_order_and_suffixes(monkeypatch):
    calls = []
    ctx, task, paths, targets, deleted = object(), object(), ("tests/a.py",), {"id"}, ("gone",)
    monkeypatch.setattr(
        deferred_collected.base_fixture_names,
        "check_with_base",
        lambda *args: calls.append(("base", args)),
    )
    monkeypatch.setattr(
        deferred_collected,
        "check",
        lambda *args: calls.append(("collect", args)),
    )
    deferred_collected.check_with_base(ctx, task, paths, "run", targets, deleted)
    assert calls == [
        ("base", (ctx, task, paths, "run", deleted)),
        ("collect", (ctx, task, paths, targets, "runc")),
    ]


def test_collect_all_passes_context_and_numbered_batch_ids(monkeypatch):
    files = tuple(f"f{i}.py" for i in range(deferred_collected.MAX_FILES + 1))
    ctx = object()
    calls = []

    def collect(*args: Any) -> Collected:
        calls.append(args)
        return Collected()

    monkeypatch.setattr(deferred_collected, "_collect", collect)
    assert deferred_collected._collect_all(ctx, "commit", files, "rid") == Collected()
    assert calls == [
        (ctx, "commit", files[:50], "rid0"),
        (ctx, "commit", files[50:], "rid1"),
    ]


@pytest.mark.parametrize("bad_reason", [None, "XXcollect-no-recordXX", "COLLECT-NO-RECORD"])
def test_collect_all_missing_batch_reason_defaults_exactly(monkeypatch, bad_reason):
    monkeypatch.setattr(deferred_collected, "_WHY", deferred_collected._WHY)
    token = deferred_collected._WHY.set({"rid0": bad_reason} if bad_reason else {})
    try:
        monkeypatch.setattr(deferred_collected, "_collect", lambda *a: None)
        assert deferred_collected._collect_all(None, "c", ("a.py",), "rid") is None
        # A batch reason transfers to the parent id; a missing one has the contract fallback.
        expected = bad_reason or "collect-no-record"
        assert deferred_collected._why()["rid"] == expected
    finally:
        deferred_collected._WHY.reset(token)


def test_collect_all_merges_and_sorts_every_record_field(monkeypatch):
    chunks = iter(
        [
            Collected(("z",), ("e2",), ("s2",)),
            Collected(("a",), ("e1",), ("s1",)),
        ]
    )
    monkeypatch.setattr(deferred_collected, "_collect", lambda *args: next(chunks))
    names = tuple(f"f{i}.py" for i in range(51))
    assert deferred_collected._collect_all(None, "c", names, "r") == Collected(
        ("a", "z"), ("e1", "e2"), ("s1", "s2")
    )


def test_collect_passes_exact_spec_context_run_id_and_removes_log_parent(monkeypatch, tmp_path):
    ctx, run = object(), SimpleNamespace(log_path=tmp_path / "log-dir" / "run.log")
    run.log_path.parent.mkdir()
    (run.log_path.parent / "record").write_text("private evidence")
    calls = []

    def verifiers(*args: Any) -> tuple[Any, ...]:
        calls.append(args)
        return (run,)

    monkeypatch.setattr(deferred_collected, "run_verifiers", verifiers)
    monkeypatch.setattr(deferred_collected, "read_collected", lambda got: Collected(("id",)))
    assert deferred_collected._collect(ctx, "commit", ("tests/a.py",), "run") == Collected(("id",))
    (got_ctx, commit, specs, run_id) = calls[0]
    assert (got_ctx, commit, run_id) == (ctx, "commit", "run")
    assert specs == (
        VerifierSpec("collected-974", ("tests/a.py",), collect_only=True, announce=False),
    )
    assert not run.log_path.parent.exists()


def test_collect_rejects_multiple_runs_without_reading_and_cleans_each_parent(
    monkeypatch, tmp_path
):
    runs = tuple(SimpleNamespace(log_path=tmp_path / f"log-{i}" / "run.log") for i in range(2))
    for run in runs:
        run.log_path.parent.mkdir()
    read_calls = []
    monkeypatch.setattr(deferred_collected, "run_verifiers", lambda *args: runs)
    monkeypatch.setattr(
        deferred_collected, "read_collected", lambda run: read_calls.append(run) or Collected()
    )
    assert deferred_collected._collect(None, "c", ("a.py",), "r") is None
    assert read_calls == []
    assert all(not run.log_path.parent.exists() for run in runs)


def test_collect_fault_and_missing_record_reasons_are_stable(monkeypatch):
    token = deferred_collected._WHY.set({})
    try:
        monkeypatch.setattr(
            deferred_collected, "run_verifiers", lambda *a: (_ for _ in ()).throw(OSError())
        )
        assert deferred_collected._collect(None, "c", (), "fault") is None
        assert deferred_collected._why()["fault"] == "collect-fault"
        monkeypatch.setattr(deferred_collected, "run_verifiers", lambda *a: ())
        assert deferred_collected._collect(None, "c", (), "empty") is None
        assert deferred_collected._why()["empty"] == "collect-no-record"
    finally:
        deferred_collected._WHY.reset(token)


def test_check_skips_non_python_and_conftest_paths(monkeypatch):
    calls = []
    monkeypatch.setattr(deferred_collected, "conftest_of", lambda paths: ("tests/conftest.py",))
    monkeypatch.setattr(
        deferred_collected,
        "_collect_all",
        lambda *args: calls.append(args) or Collected(),
    )
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    task = SimpleNamespace(test_commit="t", base_commit="b")
    deferred_collected.check(None, task, ("tests/conftest.py", "tests/data.txt"), set(), "r")
    assert calls == []


def test_check_routes_context_files_and_run_ids_through_both_commits(monkeypatch):
    calls = []
    ctx = object()
    task = SimpleNamespace(test_commit="test", base_commit="base")
    paths = ("tests/conftest.py", "tests/a.py", "tests/data.txt", "tests/b.py")
    monkeypatch.setattr(deferred_collected, "conftest_of", lambda paths: ("tests/conftest.py",))
    seen_tasks = []

    def exists(got_task: Any, path: str) -> bool:
        seen_tasks.append(got_task)
        return path == "tests/a.py"

    monkeypatch.setattr(deferred_collected, "_exists", exists)

    def collect(*args: Any) -> Collected:
        calls.append(args)
        return Collected()

    monkeypatch.setattr(deferred_collected, "_collect_all", collect)
    monkeypatch.setattr(
        deferred_collected, "_probe_files", lambda task, files: ("tests/base_probe.py",)
    )
    deferred_collected.check(ctx, task, paths, set(), "rid")
    assert seen_tasks == [task, task]
    assert calls == [
        (ctx, "test", ("tests/a.py", "tests/b.py"), "ridt"),
        (ctx, "base", ("tests/a.py",), "ridb"),
    ]


def test_failed_test_commit_run_probes_the_base_with_context_task_and_probe_id(monkeypatch):
    ctx = object()
    task = SimpleNamespace(test_commit="test", base_commit="base")
    calls, probes = [], []
    records = iter([None, Collected()])
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    monkeypatch.setattr(
        deferred_collected, "_collect_all", lambda *args: calls.append(args) or next(records)
    )
    monkeypatch.setattr(
        deferred_collected, "_probe_files", lambda *args: probes.append(args) or ("tests/p.py",)
    )
    with pytest.raises(deferred_collected.TestCommitRejected):
        deferred_collected.check(ctx, task, ("tests/a.py",), set(), "rid")
    assert probes == [(task, ("tests/a.py",))]
    assert calls[1] == (ctx, "base", ("tests/p.py",), "ridp")


def test_collect_failure_message_uses_fallback_and_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(deferred_collected, "_collect_all", lambda *args: None)
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    monkeypatch.setattr(deferred_collected, "_probe_files", lambda *args: ("tests/old.py",))
    calls = iter([None, Collected()])
    monkeypatch.setattr(deferred_collected, "_collect_all", lambda *args: next(calls))
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with caplog.at_level("WARNING", logger=deferred_collected.LOG.name):
        with pytest.raises(deferred_collected.TestCommitRejected) as err:
            deferred_collected.check(None, task, ("tests/new.py",), set(), "run")
    assert "DEFERRED_TESTS_COLLECT_FAILED" in err.value.code
    assert "collect-no-record" in str(err.value)
    assert str(err.value) == (
        "DEFERRED_TESTS_COLLECT_FAILED: pytest's collect-only run on the test commit gave no "
        "usable record (collect-no-record: timeout, crash, output over the cap or no report), "
        "so Cairnwork cannot tell which tests it adds; rerun the task"
    )
    assert [r.getMessage() for r in caplog.records] == [
        "DEFERRED_TESTS_COLLECT_FAILED: collect-no-record (1 files)"
    ]


def test_unseen_collected_id_refusal_has_stable_reader_guidance(monkeypatch):
    records = iter([Collected(("tests/a.py::test_hidden",)), Collected()])
    monkeypatch.setattr(deferred_collected, "_collect_all", lambda *args: next(records))
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    task = SimpleNamespace(test_commit="t", base_commit="b")
    with pytest.raises(deferred_collected.TestCommitRejected) as err:
        deferred_collected.check(None, task, ("tests/a.py",), set(), "r")
    assert err.value.code == "DEFERRED_TESTS_UNREADABLE"
    assert str(err.value) == (
        "DEFERRED_TESTS_UNREADABLE: pytest collects 1 new test(s) in the changed files that the "
        "reader cannot see; Cairnwork cannot tell which tests the commit adds, so it cannot "
        "check they ran; write them as plain Test* classes or top-level test functions"
    )


# equivalent: line 86 `if on_base` -> `or True`: _collect_all over no files returns an empty
# Collected() equal to NONE, so nothing observable changes.
