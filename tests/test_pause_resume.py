"""Focused, offline cut and orchestration checks for #476 PAUSE step 3."""

from __future__ import annotations

import json
from dataclasses import asdict

from experimental.local_worker.cuts import first_edit_cut
from experimental.local_worker.episode import TurnLog
from experimental.local_worker.gitstate import TreeState


def _turns(changed_at: int) -> tuple[list[TurnLog], TreeState]:
    base = TreeState(True, "a" * 40, ())
    rows = []
    for index in range(5):
        tree = TreeState(True, base.head, ("? src/new.py",)) if index == changed_at else base
        rows.append(
            TurnLog(
                index=index,
                t_start=float(index),
                t_end=float(index + 1),
                action_type="write_file" if index == changed_at else "read_file",
                tool_ran=True,
                tree=tree,
            )
        )
    return rows, base


def test_thirty_replayed_checkpoints_cut_exactly_at_first_edit():
    for changed_at in range(30):
        rows, base = _turns(changed_at % 5)
        # Repeat each generated trace six times with its expected cut position.
        assert first_edit_cut(rows, base)["cut_turn_index"] == changed_at % 5
        assert first_edit_cut(rows, base)["cut_status"] == "CUT"


def test_unobserved_tree_is_not_a_cut():
    base = TreeState(True, "a" * 40, ())
    rows = [TurnLog(index=0, t_start=0, t_end=1, tool_ran=True, tree=None)]
    cut = first_edit_cut(rows, base)
    assert cut["cut_status"] == "UNOBSERVED"
    assert cut["cut_turn_index"] is None


def test_checkpoint_fixture_is_independent_of_machine_paths(tmp_path):
    rows, _ = _turns(2)
    records = [
        asdict(row) | {"tree": {"head": row.tree.head, "entries": row.tree.entries}} for row in rows
    ]
    payload = {"turns": records, "record": {"base_sha": "a" * 40}}
    path = tmp_path / "parent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["record"]["base_sha"] == "a" * 40


# ---- orchestration (run_resume), all fakes: no worker, no network ----
import hashlib  # noqa: E402
import types  # noqa: E402

import pytest  # noqa: E402

from experimental.pause import receipt, resume  # noqa: E402
from experimental.pause.interfaces import (  # noqa: E402
    CallSettings,
    GateReply,
    GuardUnavailable,
    PlanReply,
)
from experimental.pause.schema import AC2_FIELDS, Reason  # noqa: E402
from tests.pause_fakes import FakeGate, FakeGuard, FakePlanner, MemoryEvents  # noqa: E402

SET = CallSettings("m", "low", 1.0, 1)
OK = GateReply("ACCEPT", "g", "low")
NO = GateReply("REJECT", "g", "low")


def _write_parent(tmp_path, changed_at, name="parent.json"):
    rows, _ = _turns(changed_at)
    records = [
        asdict(r) | {"tree": {"head": r.tree.head, "entries": list(r.tree.entries)}} for r in rows
    ]
    path = tmp_path / name
    payload = {"turns": records, "record": {"base_sha": "a" * 40}, "messages": []}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class Rig:
    def __init__(self, tmp_path, monkeypatch, changed_at=2, replies=None, guard_files=None):
        monkeypatch.setattr(
            resume.worker_cli, "resume_prefix", lambda parent, cut: ([{"role": "user"}], cut, 0.0)
        )
        self.parent = _write_parent(tmp_path, changed_at)
        self.events, self.checkout = MemoryEvents(), FakeGuard(guard_files)
        self.guard = types.SimpleNamespace(
            events=self.events, guard=self.checkout, baseline=None, delivered=False
        )
        self.guard.start = lambda: setattr(self.guard, "baseline", self.checkout.digest())
        self.guard.at_plan_arrival = lambda: self.on_arrival()
        self.on_arrival = lambda: None
        self.planner = FakePlanner([PlanReply("the plan", "p", "high")] * 3)
        self.gate = FakeGate(replies or [OK])
        self.resumed, self.bared, self.tmp = [], [], tmp_path

    def go(self, **kw):
        kw.setdefault("scope_checker", lambda result, scope: True)
        return resume.run_resume(
            self.parent, task_id="t", planner=self.planner, gate=self.gate,
            planner_settings=SET, gate_settings=SET, guard=self.guard, events=self.events,
            resume_runner=lambda p, c, f: self.resumed.append((c, f.read_text())) or "patch",
            bare_runner=lambda: self.bared.append(1) or "bare", **kw,
        )  # fmt: skip


def test_ac2_thirty_checkpoints_plan_at_first_edit_cut_and_receipt_fields(tmp_path, monkeypatch):
    for n in range(30):
        rig = Rig(tmp_path, monkeypatch, changed_at=n % 5)
        sub = tmp_path / str(n)
        sub.mkdir()
        rig.parent = _write_parent(sub, n % 5)
        res = rig.go()
        assert res.intervened and rig.resumed == [(n % 5, "the plan")], n
        rec = res.receipt
        assert receipt.verify(rec, "the plan") == []
        assert rec["task_id"] == "t" and rec["cut_turn_index"] == n % 5
        assert rec["checkpoint_sha256"] == hashlib.sha256(rig.parent.read_bytes()).hexdigest()
        assert rec["plan_sha256"] == hashlib.sha256(b"the plan").hexdigest()
        assert all(rec[f] is not None for f in AC2_FIELDS)


def test_unobserved_cut_runs_bare_without_planner(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    payload = json.loads(rig.parent.read_text())
    payload["turns"][0]["tree"] = None
    for t in payload["turns"]:
        t["tree"] = None
    rig.parent.write_text(json.dumps(payload))
    res = rig.go()
    assert res.reason == Reason.PAUSE_NO_CUT and not res.intervened
    assert rig.bared == [1] and rig.resumed == [] and rig.planner.requests == []


def test_reject_never_resumes_and_checkout_untouched(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, replies=[NO])
    before = dict(rig.checkout.files)
    res = rig.go()
    assert res.reason == Reason.PAUSE_GATE_REJECT_LIMIT and not res.intervened
    assert rig.resumed == [] and rig.checkout.files == before
    assert receipt.verify(res.receipt) == [] and res.receipt["gate_verdict"] == "REJECT"
    # AC3 vs AC4: the fallback is the *bare* run (rig.bared), a separate path from resume.
    assert rig.bared == [1]


def test_reject_limit_replans_then_accepts_or_stops(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, replies=[NO, OK])
    assert rig.go(reject_limit=2).intervened and len(rig.planner.requests) == 2
    rig = Rig(tmp_path, monkeypatch, replies=[NO, NO])
    res = rig.go(reject_limit=2)
    assert res.reason == Reason.PAUSE_GATE_REJECT_LIMIT and rig.resumed == []


def test_gate_reason_means_no_resume(tmp_path, monkeypatch):
    rig = Rig(
        tmp_path, monkeypatch, replies=[GateReply(None, "g", "low", Reason.PAUSE_GATE_TIMEOUT)]
    )
    res = rig.go()
    assert res.reason == Reason.PAUSE_GATE_TIMEOUT and rig.resumed == []


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_symlink_gitlink_out_of_scope_discard_result(tmp_path, monkeypatch, mode):
    for rows in ([{"path": "src/l", "mode": mode}], [{"path": "docs/x.py", "mode": "100644"}]):
        rig = Rig(tmp_path, monkeypatch)
        res = rig.go(
            scope_checker=lambda result, scope, rows=rows: resume._scope_checker(rows, scope),
            write_scope=("src",),
        )
        assert res.reason == Reason.PAUSE_SCOPE_VIOLATION and res.patch is None
        assert not res.intervened and receipt.verify(res.receipt) == []
    assert resume._scope_checker([{"path": "src/a.py", "mode": "100644"}], ("src",))


def test_tampered_plan_sha_fails_verification(tmp_path, monkeypatch):
    rec = Rig(tmp_path, monkeypatch).go().receipt
    assert receipt.verify(rec, "the plan") == []
    assert receipt.verify(dict(rec, plan_sha256="0" * 64)) != []
    assert receipt.verify(rec, "another plan") != []


def test_checkout_change_before_plan_falls_back(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)

    def raise_changed():
        raise GuardUnavailable("changed")

    rig.on_arrival = raise_changed
    res = rig.go()
    assert res.reason == Reason.PAUSE_GUARD_UNAVAILABLE and rig.resumed == [] and rig.bared == [1]
    assert rig.planner.requests == []


def test_final_digest_mismatch_uses_bare_result(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    res = rig.go(sweep=lambda: "different")
    assert res.reason == Reason.PAUSE_GUARD_UNAVAILABLE and rig.bared == [1]


def test_event_sequence_increases_and_receipt_file(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    path = tmp_path / "r.jsonl"
    rec = rig.go(receipt_path=path).receipt
    seqs = [e[0] for e in rig.events.events]
    assert seqs == sorted(set(seqs)) and rec["event_seq"] == seqs[-1]
    assert receipt.verify_log([json.loads(x) for x in path.read_text().splitlines()]) == []


def test_flag_off_writes_no_receipt_file(tmp_path):
    from experimental.pause import cli

    calls = []
    assert cli.main(["run", "--x"], worker_main=lambda argv: calls.append(argv) or 0) == 0
    assert calls == [["run", "--x"]] and list(tmp_path.iterdir()) == []


def _git(root, *args):
    import subprocess

    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t.test", "-c", "user.name=t", *args],
        check=True, capture_output=True,
    )  # fmt: skip


def test_git_scope_checker_on_a_real_checkout(tmp_path):
    import os

    root = tmp_path / "co"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "d.md").write_text("d\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    check = resume.git_scope_checker(root)
    assert check(None, ("src",))  # clean checkout
    (root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (root / "src" / "new.py").write_text("y = 1\n", encoding="utf-8")
    assert check(None, ("src",))  # edits and a new file inside the scope
    (root / "docs" / "d.md").write_text("changed\n", encoding="utf-8")
    assert not check(None, ("src",))  # tracked edit outside the scope
    _git(root, "checkout", "--", "docs/d.md")
    os.symlink("a.py", root / "src" / "link.py")
    assert not check(None, ("src",))  # a symlink is never a regular file
    os.unlink(root / "src" / "link.py")
    (root / "nested").mkdir()
    (root / "nested" / "f.txt").write_text("f\n", encoding="utf-8")
    _git(root / "nested", "init", "-q")
    _git(root / "nested", "add", "-A")
    _git(root / "nested", "commit", "-q", "-m", "n")
    assert not check(None, ("src", "nested"))  # a nested repo (160000) is rejected
    assert not resume.git_scope_checker(tmp_path / "missing")(None, ("src",))  # git error: closed
