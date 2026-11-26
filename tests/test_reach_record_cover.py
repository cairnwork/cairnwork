"""#1820: the first reach run always happens; what it cannot name is checked by text; the rerun
leaves the dropped files out with --ignore."""

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.deferred_overlay import TestCommitRejected
from tests import test_reach_record as base
from tests.test_reach_record import CODE, CTX, _record

task = base.task  # the same fixture


class Specs:
    def __init__(self, monkeypatch, records):
        self.seen, self.records = [], list(records)
        monkeypatch.setattr(bfn, "run_verifiers", self)

    def __call__(self, ctx, commit, specs, run_id, **kwargs):
        from types import SimpleNamespace

        self.seen.append(specs[0])
        rec = self.records.pop(0) if self.records else None
        return (SimpleNamespace(fixture_names=frozenset() if rec else None, collect_record=rec),)


def _over(task, monkeypatch, n=bfn.MAX_TARGETS + 2):
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(n))
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: (*changed, "tests/test_a.py"))
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 0)  # a base tree over the cap: one tree file
    return changed


def test_over_the_cap_an_edit_the_first_run_did_not_name_gets_a_run_of_its_own(task, monkeypatch):
    changed = _over(task, monkeypatch)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: pytest.fail("a collected file needs no text"))
    specs = Specs(monkeypatch, [_record(task), _record(task)])
    bfn.check_with_base(CTX, task, changed, "r")
    assert len(specs.seen) == 2
    assert set(changed[: bfn.MAX_TARGETS]) <= set(specs.seen[0].targets)
    assert specs.seen[1].targets == changed[bfn.MAX_TARGETS :]


def test_that_run_can_refuse_on_what_the_unnamed_edit_loads(task, monkeypatch):
    changed = _over(task, monkeypatch)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    Specs(monkeypatch, [_record(task), _record(task, sys_path=("tests/data/lib.zip",))])
    with pytest.raises(TestCommitRejected) as caught:
        bfn.check_with_base(CTX, task, (*changed, "tests/data/lib.zip"), "r")
    assert caught.value.code == CODE


@pytest.mark.parametrize("quiet", [True, False])
def test_uncovered_edited_files_decide_by_their_text(task, monkeypatch, quiet):
    changed = _over(task, monkeypatch)
    asked = []
    monkeypatch.setattr(bfn, "_quiet", lambda t, p: asked.append(tuple(p)) or quiet)
    Specs(monkeypatch, [_record(task), None])  # the unnamed edits could not be collected
    if quiet:
        bfn.check_with_base(CTX, task, changed, "r")
    else:
        with pytest.raises(TestCommitRejected) as caught:
            bfn.check_with_base(CTX, task, changed, "r")
        assert caught.value.code == CODE
    assert asked == [changed[bfn.MAX_TARGETS :]]


def test_the_rerun_drops_what_main_dropped_before_it_drops_everything(task, monkeypatch):
    changed = _over(task, monkeypatch)
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 300)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    specs = Specs(monkeypatch, [None, _record(task)])
    bfn.check_with_base(CTX, task, changed, "r")
    kept = set(specs.seen[1].targets) & set(changed)
    assert kept == set(changed[bfn.MAX_TARGETS :])  # no weaker than main's rerun
    assert set(specs.seen[1].ignore) == set(changed[: bfn.MAX_TARGETS])


def test_the_rerun_ignores_the_dropped_files(task, monkeypatch):
    changed = ("tests/test_a.py",)
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    specs = Specs(monkeypatch, [None, _record(task)])
    bfn.check_with_base(CTX, task, changed, "r")
    assert specs.seen[0].ignore == ()
    assert specs.seen[1].ignore == changed


def test_a_dropped_healthy_file_is_found_by_halving_and_can_refuse(task, monkeypatch):
    """One broken and one healthy dropped file: the pair fails, the healthy one alone is read."""
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)  # the broken one is quiet
    seen = Specs(
        monkeypatch,
        [None, _record(task), None, None, _record(task, sys_path=("tests/data/lib.zip",))],
    )
    changed = ("tests/sib/test_s.py", "tests/test_a.py", "tests/data/lib.zip")
    with pytest.raises(TestCommitRejected) as caught:
        bfn.check_with_base(CTX, task, changed, "r")
    assert caught.value.code == CODE
    assert [s.targets for s in seen.seen[2:]] == [
        ("tests/sib/test_s.py", "tests/test_a.py"),
        ("tests/sib/test_s.py",),
        ("tests/test_a.py",),
    ]


def test_the_extra_runs_are_bounded_and_the_rest_go_by_text(task, monkeypatch):
    monkeypatch.setattr(bfn, "MAX_EXTRA_RUNS", 3)
    asked = []
    monkeypatch.setattr(bfn, "_quiet", lambda t, p: asked.append(tuple(p)) or False)
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(8))
    Specs(monkeypatch, [])
    records, unread = bfn._collect_all(
        CTX,
        task,
        changed,
        "r",
    )
    assert records == () and set(unread) == set(changed)
