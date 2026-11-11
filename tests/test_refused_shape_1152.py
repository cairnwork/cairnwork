from __future__ import annotations

import json

import pytest

from cairnwork import blackbox, route, verify_cli
from cairnwork.pipeline import TaskRejected
from cairnwork.runlock import RunLockBusy
from experimental.acceptance_outcomes.ledger import PRE_CORE, _verdict_of
from experimental.plain_result.card import card
from experimental.plain_result.summary import summarize


@pytest.mark.parametrize("cli", [verify_cli, route])
def test_task_policy_refusal_has_uniform_shape(cli, tmp_path, monkeypatch, capsys):
    def reject(*args):
        raise TaskRejected("TASK_ALLOWED_FILES_PROTECTED", "protected path")

    monkeypatch.setattr(cli, "_run" if cli is verify_cli else "_dispatch", reject)
    args = (
        [
            "--state",
            str(tmp_path),
            "run",
            "--repo-path",
            str(tmp_path),
            "--base",
            "a" * 40,
            "--candidate",
            "b" * 40,
            "--allowed-file",
            "src/x.py",
            "--verifier",
            "true",
        ]
        if cli is verify_cli
        else ["--state", str(tmp_path), "check"]
    )
    if cli is route:
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("{}"))
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"status", "verified", "reason", "error", "detail"}
    assert result == {
        "status": "REFUSED",
        "verified": False,
        "reason": "TASK_ALLOWED_FILES_PROTECTED",
        "error": "TASK_ALLOWED_FILES_PROTECTED",
        "detail": "TASK_ALLOWED_FILES_PROTECTED: protected path",
    }


def test_non_task_error_still_has_no_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        route, "_dispatch", lambda *args: (_ for _ in ()).throw(RunLockBusy("busy"))
    )
    assert route.main(["--state", str(tmp_path), "recover"]) == 2
    assert "status" not in json.loads(capsys.readouterr().out)


def test_verify_internal_error_still_has_no_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        verify_cli, "_run", lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(verify_cli, "tests_consumed", lambda *args: False)
    assert (
        verify_cli.main(
            [
                "--state",
                str(tmp_path),
                "run",
                "--repo-path",
                str(tmp_path),
                "--base",
                "a" * 40,
                "--candidate",
                "b" * 40,
                "--allowed-file",
                "src/x.py",
                "--verifier",
                "true",
            ]
        )
        == 1
    )
    assert "status" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("code", ["TASK_ALLOWED_FILES_PROTECTED", "BAD_SCOPE"])
def test_refused_shape_preserves_old_plain_summary(code):
    old = summarize({"error": code, "detail": "policy"})
    new = summarize(
        {"status": "REFUSED", "verified": False, "reason": code, "error": code, "detail": "policy"}
    )
    assert new["state"] == old["state"]


@pytest.mark.parametrize(
    "record",
    [
        {
            "status": "REFUSED",
            "reason": "BAD_SCOPE",
            "error": "TASK_ALLOWED_FILES_PROTECTED",
            "verified": False,
        },
        # reason == error, so only the `verified is False` check keeps these out of not_checked
        {
            "status": "REFUSED",
            "reason": "TASK_ALLOWED_FILES_PROTECTED",
            "error": "TASK_ALLOWED_FILES_PROTECTED",
        },
        {
            "status": "REFUSED",
            "reason": "TASK_ALLOWED_FILES_PROTECTED",
            "error": "TASK_ALLOWED_FILES_PROTECTED",
            "verified": "false",
        },
    ],
)
def test_forged_refused_shape_does_not_receive_not_checked_state(record):
    assert summarize(record)["state"] != "not_checked"


@pytest.mark.parametrize("code", ["TASK_ALLOWED_FILES_PROTECTED", "BAD_SCOPE"])
def test_card_for_new_refusal_matches_old_error_record(code):
    old = card({"error": code, "detail": "policy"})
    new = card(
        {
            "status": "REFUSED",
            "verified": False,
            "reason": code,
            "error": code,
            "detail": "policy",
        }
    )
    # Exclude only `source`, which echoes the input record and necessarily has different shape.
    assert {key: value for key, value in new.items() if key != "source"} == {
        key: value for key, value in old.items() if key != "source"
    }


def test_outcomes_keep_plain_refusal_pre_core_but_real_run_refusal():
    plain = {
        "status": "REFUSED",
        "verified": False,
        "reason": "BAD_SCOPE",
        "error": "BAD_SCOPE",
    }
    assert _verdict_of(plain) == PRE_CORE
    assert _verdict_of({**plain, "core_status": "REFUSED"}) == "REFUSED"


def test_verify_cli_blackbox_rejection_has_uniform_shape(tmp_path, monkeypatch, capsys):
    def reject(*args):
        raise blackbox.BlackboxRejected("bad blackbox declaration")

    monkeypatch.setattr(verify_cli, "_run", reject)
    args = [
        "--state",
        str(tmp_path),
        "run",
        "--repo-path",
        str(tmp_path),
        "--base",
        "a" * 40,
        "--candidate",
        "b" * 40,
        "--allowed-file",
        "src/x.py",
        "--verifier",
        "true",
    ]
    assert verify_cli.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "REFUSED",
        "verified": False,
        "reason": blackbox.BAD,
        "error": blackbox.BAD,
        "detail": "bad blackbox declaration",
    }
