"""#1854 review: the extra base runs (changed files the first run did not name) and the primary
record. Each test names the move of the mutant it kills."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected

CODE = "TEST_COMMIT_CONFTEST_STATIC"
MANY = tuple(f"tests/m{n:02d}.py" for n in range(45))  # 20 named by the first run, 25 extras
CTX = SimpleNamespace(account=None)


def _repo(tmp_path, files, monkeypatch, tree):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=x", "-c", "user.email=x@x", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    git("add", "-A")
    git("commit", "-qm", "base")
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: tree)
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: None)
    return SimpleNamespace(
        repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t", test_roots=("tests",)
    )


@pytest.fixture
def many(tmp_path, monkeypatch):
    return _repo(
        tmp_path, ("tests/conftest.py", "tests/test_a.py", *MANY), monkeypatch, ("tests/test_a.py",)
    )


def entry(task, rel):
    """A record whose sys.path names ``rel`` of the checkout."""
    root = str(Path(task.repo).resolve())
    return CollectedRecord(frozenset(), (), (str(Path(root) / rel),), (), root)


def clean(task):
    return CollectedRecord(frozenset(), (), (), (), str(Path(task.repo).resolve()))


class Runs:
    """A fake run_verifiers: ``answer(targets, ignore)`` gives the record or None."""

    def __init__(self, monkeypatch, answer):
        self.calls, self.answer = [], answer
        monkeypatch.setattr(bfn, "run_verifiers", self)

    def __call__(self, ctx, commit, specs, run_id, **kwargs):
        spec = specs[0]
        self.calls.append((spec.targets, spec.ignore))
        record = self.answer(spec.targets, spec.ignore)
        return (
            SimpleNamespace(fixture_names=frozenset() if record else None, collect_record=record),
        )


def check(task, paths):
    bfn.check_with_base(CTX, task, paths, "r")


def refused(task, paths):
    with pytest.raises(TestCommitRejected) as caught:
        check(task, paths)
    assert caught.value.code == CODE


def test_a_file_beyond_the_first_twenty_edits_is_checked_by_a_run_of_its_own(many, monkeypatch):
    """Kills: no extra runs; only the first record checked; extras limited to the first 20."""
    bad = "tests/m42.py"  # the 23rd of the 25 extras
    runs = Runs(monkeypatch, lambda t, i: entry(many, bad) if bad in t else clean(many))
    refused(many, MANY)
    assert any(bad in targets for targets, _ in runs.calls[1:])


def test_a_group_that_cannot_be_collected_is_halved_to_find_the_bad_file(many, monkeypatch):
    """Kills: halving removed; budget 12 reduced to 1."""
    bad, broken = "tests/m42.py", "tests/m30.py"

    def answer(targets, ignore):
        if broken in targets:
            return None
        return entry(many, bad) if bad in targets else clean(many)

    Runs(monkeypatch, answer)
    refused(many, MANY)


def test_the_extra_runs_stop_at_twelve_and_add_nothing_when_all_fail(many, monkeypatch):
    runs = Runs(monkeypatch, lambda t, i: clean(many) if "tests/test_a.py" in t else None)
    check(many, MANY)  # nothing was learnt from the extras: the verdict stays the first run's
    extra = [c for c in runs.calls if "tests/test_a.py" not in c[0]]
    assert len(extra) == 12


def test_the_extra_run_that_fails_leaves_the_primary_record_and_marks_it_incomplete(
    many, monkeypatch
):
    """Primary record kept when the extra runs fault; result incomplete (refused)."""
    Runs(monkeypatch, lambda t, i: clean(many))

    def boom(*args, **kwargs):
        raise RuntimeError("extra run fault")

    monkeypatch.setattr(bfn, "_collect_all", boom)
    refused(many, MANY)  # incomplete: fail closed
    records, complete = bfn.reach_record(CTX, many, MANY, "r")
    assert len(records) == 1 and complete is False


def test_the_primary_record_still_refuses_when_the_extra_runs_fault(many, monkeypatch):
    Runs(monkeypatch, lambda t, i: entry(many, "tests/m00.py"))
    monkeypatch.setattr(bfn, "_collect_all", lambda *a, **k: 1 / 0)
    refused(many, MANY)


def test_with_no_tree_file_left_the_rerun_names_the_test_roots(tmp_path, monkeypatch):
    """Kills: the roots fallback removed (no second run)."""
    task = _repo(tmp_path, ("tests/m00.py",), monkeypatch, ("tests/m00.py",))
    runs = Runs(monkeypatch, lambda t, i: None if "tests/m00.py" in t else clean(task))
    check(task, ("tests/m00.py",))
    assert runs.calls[1] == (("tests",), ("tests/m00.py",))
