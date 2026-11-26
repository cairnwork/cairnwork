"""#1820: the first reach run always happens; what it cannot name is checked by text; the rerun
leaves the dropped files out with --ignore."""

import pytest

from cairnwork import base_fixture_names as bfn
from cairnwork.deferred_overlay import TestCommitRejected
from tests import test_reach_record as base
from tests.test_reach_record import CODE, CTX, Runs, _record, check

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


class Smart:
    """A fake run_verifiers where a run gives no record when it names a ``broken`` file."""

    def __init__(self, monkeypatch, broken, record):
        self.seen, self.broken, self.record = [], set(broken), record
        monkeypatch.setattr(bfn, "run_verifiers", self)

    def __call__(self, ctx, commit, specs, run_id, **kwargs):
        from types import SimpleNamespace

        self.seen.append(specs[0])
        bad = self.broken & (set(specs[0].targets) - set(specs[0].ignore))
        rec = None if bad else self.record
        return (SimpleNamespace(fixture_names=frozenset() if rec else None, collect_record=rec),)


def test_the_rerun_ignores_only_the_files_that_cannot_be_collected(task, monkeypatch):
    changed = ("tests/sib/test_s.py", "tests/test_a.py")
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    smart = Smart(monkeypatch, {"tests/sib/test_s.py"}, _record(task))
    bfn.check_with_base(CTX, task, changed, "r")
    last = smart.seen[-1]
    assert last.ignore == ("tests/sib/test_s.py",)
    assert "tests/test_a.py" in last.targets  # the healthy edit stays named: its state still loads


@pytest.mark.parametrize("count", [3, bfn.MAX_TARGETS + 5])
def test_many_edits_with_one_broken_are_found_by_halving_and_the_rest_stay_named(
    task, monkeypatch, count
):
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(count))
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: (*changed, "tests/test_a.py"))
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    smart = Smart(monkeypatch, {changed[0]}, _record(task))
    bfn.check_with_base(CTX, task, changed, "r")
    full = [s for s in smart.seen if s.ignore == (changed[0],)]
    assert full and set(changed[1 : bfn.MAX_TARGETS]) <= set(full[0].targets)
    assert len(smart.seen) <= 2 + 2 * bfn.MAX_EXTRA_RUNS


def test_a_healthy_file_next_to_a_broken_one_can_refuse(task, monkeypatch):
    """The state a sibling sets up is there in the run that ignores only the broken file."""
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda *a: True)
    Smart(monkeypatch, {"tests/sib/test_s.py"}, _record(task, sys_path=("tests/data/lib.zip",)))
    with pytest.raises(TestCommitRejected) as caught:
        bfn.check_with_base(
            CTX, task, ("tests/sib/test_s.py", "tests/test_a.py", "tests/data/lib.zip"), "r"
        )
    assert caught.value.code == CODE


def test_a_broken_file_decides_by_text_when_the_run_without_it_succeeds(task, monkeypatch):
    asked = []
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "_quiet", lambda t, p: asked.append(tuple(p)) or False)
    Smart(monkeypatch, {"tests/sib/test_s.py"}, _record(task))
    with pytest.raises(TestCommitRejected) as caught:
        bfn.check_with_base(CTX, task, ("tests/sib/test_s.py",), "r")
    assert caught.value.code == CODE
    assert asked == [("tests/sib/test_s.py",)]


def test_the_extra_runs_are_bounded_and_the_rest_go_by_text(task, monkeypatch):
    monkeypatch.setattr(bfn, "MAX_EXTRA_RUNS", 3)
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(8))
    smart = Smart(monkeypatch, set(changed), _record(task))
    records, unread = bfn._collect_all(CTX, task, changed, "r")
    assert records == () and set(unread) == set(changed)
    assert len(smart.seen) == 3  # the bound, not one run per file


def test_a_group_that_succeeds_uses_one_run_and_leaves_nothing_unread(task, monkeypatch):
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(8))
    smart = Smart(monkeypatch, set(), _record(task))
    records, unread = bfn._collect_all(CTX, task, changed, "r")
    assert len(records) == 1 and unread == () and len(smart.seen) == 1


def test_more_changed_py_files_than_the_run_can_name_are_not_taken_as_covered(task, monkeypatch):
    """The first run names only MAX_TARGETS of them; it must not stand as a full record."""
    changed = tuple(f"tests/many/test_{i:02d}.py" for i in range(bfn.MAX_TARGETS + 1))
    monkeypatch.setattr(bfn, "base_tree_targets", lambda *a: (*changed, "tests/test_a.py"))
    monkeypatch.setattr(bfn, "_on_base", lambda *a: True)
    monkeypatch.setattr(bfn, "MAX_TREE_TARGETS", 0)  # the tree run names one file, not the edits
    quiet = []
    monkeypatch.setattr(bfn, "_quiet", lambda *a: quiet.append(a) or False)
    Runs(monkeypatch, [_record(task), None])  # the run of the unnamed edit gives no record
    with pytest.raises(TestCommitRejected) as caught:
        check(task, changed)
    assert caught.value.code == CODE
    assert quiet
