"""Bad R0 metadata and transcript hashing must still produce valid fallback receipts."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from test_pause_1418_r0family import SETTINGS, Gate, Planner, _case, _run, _transcript

from experimental.pause import cli, flow, receipt


class _NotJson:
    pass


def _assert_r0_fallback(result: Any, rec: dict) -> None:
    assert result.code == 7
    assert rec["intervened"] is False and rec["reason"] == "PAUSE_NO_CUT"
    assert receipt.verify(rec) == []


def _cli_run(tmp_path: Path, r0: Any, r1: Any) -> tuple[int, Path]:
    task = tmp_path / "task.json"
    task.write_text('{"task_id":"task"}')
    out = tmp_path / "out"
    out.mkdir()

    def factory(_config: str):
        return (
            Planner(),
            Gate(),
            SETTINGS,
            SETTINGS,
            lambda mode, args, **_kwargs: r0 if mode == "run" else r1,
            None,
        )

    code = cli.main(
        [
            "run",
            "--planner-config",
            "cfg",
            "--task",
            str(task),
            "--checkout",
            str(tmp_path / "checkout"),
            "--out",
            str(out),
            "--model",
            "m",
            "--context-cap",
            "1",
            "--canary-report",
            "c",
        ],
        run_factory=factory,
    )
    return code, out / cli.RECEIPTS_FILE


def _assert_cli_fallback(code: int, path: Path) -> None:
    assert code == 7 and path.is_file()
    rec = json.loads(path.read_text().splitlines()[-1])
    assert rec["reason"] == "PAUSE_NO_CUT" and receipt.verify(rec) == []


def test_r0_non_json_episode_id_keeps_valid_fallback_receipt(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    r0 = replace(r0, episode_id=_NotJson())
    _transcript(r0.transcript, [{"index": 2, "assistant_msg_index": "bad"}])
    _assert_r0_fallback(*_run(r0, r1))


def test_r0_non_json_episode_id_cli_writes_receipt(tmp_path: Path) -> None:
    r0, r1 = _case(tmp_path)
    r0 = replace(r0, episode_id=_NotJson())
    _transcript(r0.transcript, [{"index": 2, "assistant_msg_index": "bad"}])
    code, path = _cli_run(tmp_path, r0, r1)
    _assert_cli_fallback(code, path)


def test_transcript_sha_oserror_keeps_r0(tmp_path: Path, monkeypatch: Any) -> None:
    r0, r1 = _case(tmp_path)
    monkeypatch.setattr(flow, "_sha", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    _assert_r0_fallback(*_run(r0, r1))


def test_transcript_sha_oserror_cli_writes_receipt(tmp_path: Path, monkeypatch: Any) -> None:
    r0, r1 = _case(tmp_path)
    monkeypatch.setattr(flow, "_sha", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    code, path = _cli_run(tmp_path, r0, r1)
    _assert_cli_fallback(code, path)
