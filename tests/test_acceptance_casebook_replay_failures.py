"""Failure and append-only report tests for injected acceptance replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_acceptance_casebook_replay import _args, _confirmed

from experimental.acceptance_casebook import replay


def test_failure1_runner_errors_are_unreplayable(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case])
    monkeypatch.setattr(replay, "RUNNER", lambda *_: {"exit_code": 1, "verdict": None})
    assert replay.main(args) == 3
    result, summary = [json.loads(x) for x in (tmp_path / "replay.jsonl").read_text().splitlines()]
    assert result["outcome"] == "UNREPLAYABLE" and summary["complete"] is False


def test_failure2_repeat_difference_unreplayable(monkeypatch, tmp_path, lg, capsys):
    case = _confirmed(lg, capsys, "a1", "accepted_bad")
    args = _args(tmp_path, lg, [case]) + ["--repeat", "2"]
    values = iter(["accepted", "refused"])
    monkeypatch.setattr(replay, "RUNNER", lambda *_: next(values))
    assert replay.main(args) == 3
    result = json.loads((tmp_path / "replay.jsonl").read_text().splitlines()[0])
    assert result["reason"] == "NONDETERMINISTIC"


def test_failure3_no_eligible_cases(monkeypatch, tmp_path, lg):
    args = _args(tmp_path, lg, [])
    monkeypatch.setattr(replay, "RUNNER", lambda *_: "accepted")
    assert replay.main(args) == 3
    assert json.loads((tmp_path / "replay.jsonl").read_text().splitlines()[-1])["eligible"] == 0


def test_ac4e_report_rejects_tampering_and_incomplete_run(tmp_path: Path):
    path = tmp_path / "r.jsonl"
    raw = b'{"prev":"' + b"0" * 64 + b'","type":"replay_result","run":"x"}\n'
    path.write_bytes(raw)
    path.with_name(path.name + ".head").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert replay.report(path) == "INCOMPLETE_RUN"
    from experimental.acceptance_casebook.store import _append

    _append(path, {"type": "replay_summary", "run": "later"}, hashlib.sha256(raw).hexdigest())
    assert replay.report(path) == "INCOMPLETE_RUN"
    path.write_text(path.read_text().replace("result", "edited"))
    assert replay.report(path) == "REPLAY_NOT_APPEND_ONLY"


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"exit_code": False, "verdict": "VERIFIED"}, "UNREPLAYABLE"),
        ({"exit_code": False, "verdict": "FAILED"}, "UNREPLAYABLE"),
        ({"exit_code": 0.0, "verdict": "VERIFIED"}, "UNREPLAYABLE"),
        ({"exit_code": "0", "verdict": "VERIFIED"}, "UNREPLAYABLE"),
        ({"exit_code": None, "verdict": "FAILED"}, "UNREPLAYABLE"),
        ({"exit_code": "x", "verdict": "REFUSED"}, "UNREPLAYABLE"),
        ({"exit_code": True, "verdict": "REFUSED"}, "UNREPLAYABLE"),
        ({"exit_code": 0, "verdict": "VERIFIED"}, "accepted"),
        ({"exit_code": 0, "verdict": "FAILED"}, "refused"),
    ],
)
def test_only_an_integer_exit_code_maps_to_a_verdict(monkeypatch, value, expected):
    from experimental.acceptance_casebook import replay

    monkeypatch.setattr(replay, "RUNNER", lambda *_: value)
    mapped, reason, code, _ = replay._run_one({"kind": "accepted_bad"}, {}, 0)
    assert mapped == expected
    assert type(code) is int
    if expected == "UNREPLAYABLE":
        assert reason
