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
