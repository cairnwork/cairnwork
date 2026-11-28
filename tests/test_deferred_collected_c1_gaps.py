"""Focused regression checks for deferred_collected's surviving mutants."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names, deferred_collected
from cairnwork.collect_reader import Collected
from cairnwork.deferred_tests import TestCommitRejected as Rejected
from cairnwork.gitcmd import GitCommandError

PATH = "tests/test_existing.py"
CTX = object()
TASK = SimpleNamespace(repo=Path("/tmp/repo"), base_commit="base", test_commit="test")


def test_check_with_base_forwards_each_argument_to_both_checks(monkeypatch):
    base_calls = []
    collected_calls = []
    deleted = ("tests/test_deleted.py",)
    targets = {"tests/test_new.py::test_case"}

    monkeypatch.setattr(
        base_fixture_names, "check_with_base", lambda *args: base_calls.append(args)
    )
    monkeypatch.setattr(deferred_collected, "check", lambda *args: collected_calls.append(args))

    deferred_collected.check_with_base(CTX, TASK, (PATH,), "run-7", targets, deleted)

    assert base_calls == [(CTX, TASK, (PATH,), "run-7", deleted)]
    assert collected_calls == [(CTX, TASK, (PATH,), targets, "run-7c")]


def test_check_probes_a_failed_new_commit_against_the_base(monkeypatch):
    calls = []
    probes = []
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: False)
    monkeypatch.setattr(
        deferred_collected,
        "_probe_files",
        lambda task, files: probes.append((task, files)) or ("tests/test_base.py",),
    )

    def collect(ctx, commit, files, run_id):
        calls.append((ctx, commit, files, run_id))
        return None if commit == TASK.test_commit else Collected()

    monkeypatch.setattr(deferred_collected, "_collect_all", collect)

    with pytest.raises(Rejected) as err:
        deferred_collected.check(CTX, TASK, ("src/new.py",), set(), "run-8")

    assert err.value.code == "DEFERRED_TESTS_COLLECT_FAILED"
    assert calls == [
        (CTX, "test", ("src/new.py",), "run-8t"),
        (CTX, "base", ("tests/test_base.py",), "run-8p"),
    ]
    assert probes == [(TASK, ())]


def test_check_filters_non_python_and_conftest_paths_before_collecting(monkeypatch):
    collected_calls = []
    exists_calls = []
    monkeypatch.setattr(deferred_collected, "conftest_of", lambda paths: ("tests/conftest.py",))
    monkeypatch.setattr(
        deferred_collected,
        "_exists",
        lambda task, path: exists_calls.append((task, path)) or True,
    )
    monkeypatch.setattr(
        deferred_collected,
        "_collect_all",
        lambda ctx, commit, files, run_id: (
            collected_calls.append((ctx, commit, files, run_id)) or Collected()
        ),
    )

    deferred_collected.check(
        CTX,
        TASK,
        ("tests/conftest.py", "tests/test_existing.py", "README.md"),
        set(),
        "filter",
    )

    assert collected_calls == [
        (CTX, "test", ("tests/test_existing.py",), "filtert"),
        (CTX, "base", ("tests/test_existing.py",), "filterb"),
    ]
    assert exists_calls == [(TASK, "tests/test_existing.py")]


def test_missing_collect_reason_uses_the_stable_fallback(monkeypatch):
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    results = iter([None, Collected()])
    monkeypatch.setattr(deferred_collected, "_collect", lambda *args: next(results))

    with pytest.raises(Rejected) as err:
        deferred_collected.check(
            CTX,
            TASK,
            (PATH,),
            set(),
            "missing-reason",
        )

    assert "collect-no-record" in str(err.value)


def test_check_without_a_record_reason_uses_the_stable_fallback(monkeypatch):
    monkeypatch.setattr(deferred_collected, "_exists", lambda *args: True)
    results = iter([None, Collected()])
    monkeypatch.setattr(deferred_collected, "_collect_all", lambda *args: next(results))

    with pytest.raises(Rejected) as err:
        deferred_collected.check(CTX, TASK, (PATH,), set(), "empty-reason")

    assert "collect-no-record" in str(err.value)


def test_collect_all_preserves_skipped_files_across_batches(monkeypatch):
    parts = iter(
        [
            Collected(skipped_files=("tests/skip_a.py",)),
            Collected(skipped_files=("tests/skip_b.py",)),
        ]
    )
    calls = []

    def collect(*args):
        calls.append(args)
        return next(parts)

    monkeypatch.setattr(deferred_collected, "_collect", collect)
    files = tuple(f"tests/test_{n}.py" for n in range(deferred_collected.MAX_FILES + 1))

    got = deferred_collected._collect_all(CTX, "commit", files, "batch")

    assert got == Collected(skipped_files=("tests/skip_a.py", "tests/skip_b.py"))
    assert [call[0] for call in calls] == [CTX, CTX]
    assert [call[1] for call in calls] == ["commit", "commit"]
    assert [call[3] for call in calls] == ["batch0", "batch1"]


def test_collect_passes_context_and_requires_exactly_one_run(monkeypatch, tmp_path):
    seen = []
    (tmp_path / "runs").mkdir()
    run = SimpleNamespace(log_path=tmp_path / "runs" / "run.log")
    monkeypatch.setattr(
        deferred_collected,
        "run_verifiers",
        lambda ctx, commit, specs, run_id: seen.append((ctx, commit, specs, run_id)) or (run,),
    )
    monkeypatch.setattr(deferred_collected, "read_collected", lambda got: Collected())

    assert deferred_collected._collect(CTX, "commit", (PATH,), "collect") == Collected()
    ctx, commit, specs, run_id = seen[0]
    assert (ctx, commit, run_id) == (CTX, "commit", "collect")
    assert len(specs) == 1
    assert specs[0].targets == (PATH,)
    assert specs[0].collect_only and not specs[0].announce

    assert not run.log_path.parent.exists()

    monkeypatch.setattr(deferred_collected, "run_verifiers", lambda *args: ())
    assert deferred_collected._collect(CTX, "commit", (PATH,), "empty") is None
    monkeypatch.setattr(deferred_collected, "run_verifiers", lambda *args: (run, run))
    assert deferred_collected._collect(CTX, "commit", (PATH,), "two") is None


def test_probe_files_uses_only_test_python_files_from_base(monkeypatch):
    listing = (
        "README.md\nsrc/module.py\ntests/test_base.py\ntests/test_second.py\ntests/helper.txt\n"
    )
    seen = []
    monkeypatch.setattr(
        deferred_collected,
        "run_git",
        lambda repo, *args: seen.append((repo, args)) or listing,
    )

    got = deferred_collected._probe_files(TASK, ())

    assert got == ("tests/test_base.py",)
    assert seen == [(TASK.repo, ("ls-tree", "-r", "--name-only", "base"))]


def test_probe_files_keeps_changed_base_files_and_handles_git_failure(monkeypatch):
    changed = ("src/changed.py", PATH)
    monkeypatch.setattr(
        deferred_collected,
        "run_git",
        lambda *args: pytest.fail("base tree listing is unnecessary for existing files"),
    )
    assert deferred_collected._probe_files(TASK, changed) == changed

    def fail(*args):
        raise GitCommandError("unavailable")

    monkeypatch.setattr(deferred_collected, "run_git", fail)
    assert deferred_collected._probe_files(TASK, ()) == ()


def test_exists_checks_the_named_base_path_and_maps_git_failure_to_false(monkeypatch):
    seen = []
    monkeypatch.setattr(
        deferred_collected,
        "run_git",
        lambda repo, *args: seen.append((repo, args)),
    )

    assert deferred_collected._exists(TASK, PATH) is True
    assert seen == [(TASK.repo, ("cat-file", "-e", f"base:{PATH}"))]

    def fail(*args):
        raise GitCommandError("missing")

    monkeypatch.setattr(deferred_collected, "run_git", fail)
    assert deferred_collected._exists(TASK, PATH) is False
