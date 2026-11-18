"""Replay step 3a contract tests using an injected verifier runner."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_acceptance_casebook_origin as origin
from test_acceptance_casebook_confirm import _confirm

from experimental.acceptance_casebook import replay

V = "a" * 40
pytest_plugins = ["test_acceptance_casebook_origin"]

_add, _book, _ev, _list = origin._add, origin._book, origin._ev, origin._list


def _confirmed(lg: dict[str, str], capsys: Any, attempt: str, kind: str) -> dict[str, Any]:
    candidate = lg["cand"] if attempt == "a1" else lg["refused"]
    assert _add(lg, candidate, attempt, kind) == 0
    case = next(c for c in _list(lg, capsys) if c["attempt_id"] == attempt)
    _confirm(
        lg,
        case["line_sha256"],
        "--github-login",
        "human-reviewer",
        "--source-ref",
        "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-9",
        "--source-file",
        _ev(lg, "human-source"),
    )
    return next(c for c in _list(lg, capsys) if c["attempt_id"] == attempt)


def _reject(lg: dict[str, str], digest: str) -> None:
    from experimental.acceptance_casebook.confirm import check_record, confirm_case
    from experimental.acceptance_casebook.identity import resolve_actor

    who = resolve_actor(
        {},
        {
            "github_login": "human-reviewer",
            "source_ref": "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-9",
            "source_file": _ev(lg, "reject-source"),
        },
    )
    confirm_case(
        _book(lg), digest, who, "rejected", check_record("review", _ev(lg, "reject-check"))
    )


def _args(tmp_path: Path, lg: dict[str, str], cases: list[dict[str, Any]]) -> list[str]:
    clone = tmp_path / "clone"
    clone.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    tree = subprocess.check_output(["git", "-C", str(clone), "write-tree"], text=True).strip()
    commit = subprocess.check_output(
        ["git", "-C", str(clone), "commit-tree", tree, "-m", "fixture"], text=True
    ).strip()
    registry = tmp_path / "versions.json"
    registry.write_text(
        json.dumps(
            {
                commit: {
                    "tree": tree,
                    "frozen_by": "reviewer",
                    "frozen_at": "2026-11-18T00:00:00Z",
                    "note": "test",
                }
            }
        )
    )
    inputs = tmp_path / "inputs.json"
    from experimental.acceptance_casebook.origin import read_origin

    original_inputs = {}
    for case in cases:
        rec, _ = read_origin(Path(lg["db"]), case["task_id"], case["attempt_id"])
        contract = rec.request["acceptance_contract"]
        plan = rec.request["verification_plan"]
        original_inputs[case["line_sha256"]] = {
            "allowed_file": contract["allowed_paths"],
            "verifier": plan["required_verifier_ids"],
        }
    inputs.write_text(json.dumps(original_inputs))
    return [
        "replay",
        "--casebook",
        str(_book(lg)),
        "--notary-db",
        lg["db"],
        "--version",
        commit,
        "--versions",
        str(registry),
        "--inputs",
        str(inputs),
        "--cairnwork-clone",
        str(clone),
        "--out",
        str(tmp_path / "replay.jsonl"),
        "--github-login",
        "replayer",
        "--source-ref",
        "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-9",
        "--source-file",
        _ev(lg, "replayer-source"),
    ]


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lg: dict[str, str],
    cases: list[dict[str, Any]],
    result: Any,
) -> tuple[int, Path]:
    monkeypatch.setattr(replay, "RUNNER", result)
    args = _args(tmp_path, lg, cases)
    out = tmp_path / "replay.jsonl"
    return replay.main(args), out


def test_ac4a_same_mistake_and_corrected(monkeypatch, tmp_path, lg, capsys):
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    for mapped, expected in (
        (["accepted", "refused"], "same_mistake"),
        (["refused", "accepted"], "corrected"),
    ):
        calls: dict[str, int] = {"accepted_bad": 0, "refused_good": 0}
        expected_result = dict(zip(("accepted_bad", "refused_good"), mapped, strict=True))

        def runner(case, _args, counts=calls, answers=expected_result):
            counts[case["kind"]] += 1
            return answers[case["kind"]]

        rc, out = _run(monkeypatch, tmp_path / expected, lg, cases, runner)
        entries = [json.loads(line) for line in out.read_text().splitlines()]
        assert rc == 0
        assert calls == {"accepted_bad": 2, "refused_good": 2}
        assert [e["outcome"] for e in entries[:-1]] == [expected] * 2
        assert entries[-1]["type"] == "replay_summary" and entries[-1]["complete"] is True


def test_ac4a_excluded_states_not_run(monkeypatch, tmp_path, lg, capsys):
    _add(lg, lg["cand"], "a1", "accepted_bad", version="unconfirmed")
    _add(lg, lg["cand"], "a1", "accepted_bad", version="rejected")
    rejected = next(c for c in _list(lg, capsys) if c["version"] == "rejected")
    _reject(lg, rejected["line_sha256"])
    _add(lg, lg["refused"], "a2", "refused_good", version="changed")
    changed = next(c for c in _list(lg, capsys) if c["version"] == "changed")
    _confirm(
        lg,
        changed["line_sha256"],
        "--github-login",
        "human-reviewer",
        "--source-ref",
        "https://github.com/cairnwork/cairnwork/issues/772#issuecomment-9",
        "--source-file",
        _ev(lg, "origin-changed-source"),
    )
    altered_db = tmp_path / "altered.sqlite"
    import shutil

    shutil.copyfile(lg["db"], altered_db)
    with sqlite3.connect(altered_db) as db:
        names = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='core_verifications'"
            )
        ]
        for name in names:
            db.execute(f'DROP TRIGGER "{name}"')
        db.execute("UPDATE core_verifications SET recorded_at = 'changed' WHERE attempt_id = 'a2'")
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    called = []
    args = _args(tmp_path, lg, [case])
    args[args.index("--notary-db") + 1] = str(altered_db)
    monkeypatch.setattr(replay, "RUNNER", lambda *args: called.append(args) or "accepted")
    rc = replay.main(args)
    out = Path(args[args.index("--out") + 1])
    summary = json.loads(out.read_text().splitlines()[-1])
    assert rc == 0 and len(called) == 2
    assert {call[0]["line_sha256"] for call in called} == {case["line_sha256"]}
    assert summary["excluded"] == {"unconfirmed": 1, "rejected": 1, "origin_changed": 1}
    assert summary["eligible"] == 1
    assert {r["case"] for r in map(json.loads, out.read_text().splitlines()[:-1])} == {
        case["line_sha256"]
    }


def test_ac4c_runner_crash_preserves_inputs(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    watched = [_book(lg), _book(lg).with_name(_book(lg).name + ".head"), Path(lg["db"])]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched]
    _run(monkeypatch, tmp_path, lg, [case], lambda *_: (_ for _ in ()).throw(RuntimeError()))
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched]


def test_ac4b_unknown_and_invalid_versions_do_not_write(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    out = tmp_path / "replay.jsonl"
    args[args.index("--version") + 1] = "short"
    called = []
    monkeypatch.setattr(replay, "RUNNER", lambda *a: called.append(a) or "accepted")
    assert replay.main(args) != 0 and not called and not out.exists()


def test_ac4d_input_mismatch_isolated_to_case(monkeypatch, tmp_path, lg, capsys):
    cases = [
        _confirmed(lg, capsys, "a1", "accepted_bad"),
        _confirmed(lg, capsys, "a2", "refused_good"),
    ]
    args = _args(tmp_path, lg, cases)
    input_path = Path(args[args.index("--inputs") + 1])
    data = json.loads(input_path.read_text())
    data[cases[0]["line_sha256"]]["allowed_file"] = ["wrong-value"]
    input_path.write_text(json.dumps(data))
    called: list[str] = []

    def runner(case, _args):
        called.append(case["line_sha256"])
        return "accepted" if case["kind"] == "accepted_bad" else "refused"

    monkeypatch.setattr(replay, "RUNNER", runner)
    assert replay.main(args) == 3
    results = [json.loads(x) for x in Path(args[args.index("--out") + 1]).read_text().splitlines()]
    by_case = {entry["case"]: entry for entry in results if entry["type"] == "replay_result"}
    assert by_case[cases[0]["line_sha256"]]["reason"] == "REPLAY_INPUT_MISMATCH"
    assert by_case[cases[0]["line_sha256"]]["outcome"] == "UNREPLAYABLE"
    assert cases[0]["line_sha256"] not in called
    assert called.count(cases[1]["line_sha256"]) == 2


def test_ac4d_input_missing_a_recorded_field_is_a_mismatch(monkeypatch, tmp_path, lg, capsys):
    cases = [_confirmed(lg, capsys, "a1", "accepted_bad")]
    args = _args(tmp_path, lg, cases)
    input_path = Path(args[args.index("--inputs") + 1])
    data = json.loads(input_path.read_text())
    row = data[cases[0]["line_sha256"]]
    assert row.pop("allowed_file", None) is not None or row.pop("allowed_files", None) is not None
    input_path.write_text(json.dumps(data))
    called: list[str] = []
    monkeypatch.setattr(replay, "RUNNER", lambda case, _a: called.append("x") or "accepted")
    assert replay.main(args) == 3
    results = [json.loads(x) for x in Path(args[args.index("--out") + 1]).read_text().splitlines()]
    assert [r["reason"] for r in results if r["type"] == "replay_result"] == [
        "REPLAY_INPUT_MISMATCH"
    ]
    assert called == []
