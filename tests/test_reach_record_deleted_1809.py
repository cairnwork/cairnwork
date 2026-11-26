"""#1854 review: a deleted base file never changes what the primary base run names or records
(main's primary runs name only changed files); deleted ``.py`` files go to the extra runs and the
reach sets. Fake verifier runs only: no sandbox, no real collection."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.base_fixture_names import named_deleted
from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected

X, LIB, D = "tests/x.py", "tests/lib.zip", "tests/d.py"
T0 = "tests/t000.py"
TREE = tuple(f"tests/t{n:03d}.py" for n in range(301))  # over MAX_TREE_TARGETS: one tree file
CTX = SimpleNamespace(account=None)
ODD = "tests/data/my file.py"


def _task(tmp_path, monkeypatch, extra=()):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=x", "-c", "user.email=x@x", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    for name in (X, LIB, D, T0, *extra):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    git("add", "-A")
    git("commit", "-qm", "base")
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: TREE)
    monkeypatch.setattr(bfn, "conftest_dirs", lambda *a: ())
    monkeypatch.setattr(bfn, "check_conftests", lambda *a, **k: None)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    return SimpleNamespace(
        repo=tmp_path, base_commit=git("rev-parse", "HEAD"), test_commit="t", test_roots=("tests",)
    )


class Runs:
    """A fake run_verifiers: ``answer(self, targets)`` gives the record or None."""

    def __init__(self, monkeypatch, task, answer):
        self.calls, self.task, self.answer = [], task, answer
        monkeypatch.setattr(bfn, "run_verifiers", self)

    def record(self, entry=None):
        root = str(Path(self.task.repo).resolve())
        path = (f"{root}/{entry}",) if entry else ()
        return CollectedRecord(frozenset(), (), path, (), root)

    def __call__(self, ctx, commit, specs, run_id, **kwargs):
        targets = specs[0].targets
        self.calls.append(targets)
        record = self.answer(self, targets)
        return (
            SimpleNamespace(fixture_names=frozenset() if record else None, collect_record=record),
        )


def test_masking_import_of_a_deleted_file_cannot_hide_the_entry(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)
    runs = Runs(monkeypatch, task, lambda r, t: r.record(None if D in t else LIB))
    with pytest.raises(TestCommitRejected):
        bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(D,))
    assert D not in runs.calls[0]  # the primary run names only what main names


def test_deleted_file_that_cannot_be_collected_cannot_push_to_the_fallback(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)

    def answer(runs, targets):
        if D in targets:
            return None
        return runs.record(LIB if {X, T0} <= set(targets) else None)

    runs = Runs(monkeypatch, task, answer)
    with pytest.raises(TestCommitRejected):
        bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(D,))
    assert set(runs.calls[0]) == {X, T0}


def test_deleted_py_gets_a_run_of_its_own(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)
    runs = Runs(monkeypatch, task, lambda r, t: r.record(LIB if t == (D,) else None))
    with pytest.raises(TestCommitRejected):
        bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(D,))
    assert (D,) in runs.calls and D not in runs.calls[0]


def test_ordinary_deletion_passes(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)
    Runs(monkeypatch, task, lambda r, t: r.record())
    bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(D,))


def test_uncollectable_deleted_file_with_no_other_evidence_passes(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch)
    Runs(monkeypatch, task, lambda r, t: None if D in t else r.record())
    bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(D,))


def test_odd_deleted_py_stays_out_of_runs_but_is_still_refused(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch, extra=(ODD,))
    runs = Runs(monkeypatch, task, lambda r, t: r.record(ODD))
    with pytest.raises(TestCommitRejected, match="my file"):
        bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(ODD,))
    assert all(ODD not in t for t in runs.calls)


def test_odd_deletion_with_no_evidence_passes(tmp_path, monkeypatch):
    task = _task(tmp_path, monkeypatch, extra=(ODD,))
    Runs(monkeypatch, task, lambda r, t: r.record())
    bfn.check_with_base(CTX, task, (X, LIB), "r", deleted=(ODD,))


@pytest.mark.parametrize("path", ["tests/x\n::error::/pkg/mod.py", "tests/a.b/c.py", ODD])
def test_named_deleted_drops_what_no_target_can_name(path):
    assert named_deleted((path,), ("tests",)) == ()


def test_named_deleted_keeps_plain_py_only():
    deleted = ("tests/test_p_plugin.py", "tests/a/test_b-c.py", "tests/data/x.zip", "tests/conftest.py")
    assert named_deleted(deleted, ("tests",)) == deleted[:2]
