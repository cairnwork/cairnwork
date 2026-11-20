"""Core's real refusal row says FAILED_VERIFICATION, not FAILED (#751): origin and replay accept it.

The response keeps only what the check reads, in the shape a live nexus-certify row has (taken from
a real shadow preflight row: HTTP 200, integrity VALID, reason codes VERIFIER_FAILED and pytest-0).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from attest.test_attest_cli import fake_core_verify
from test_acceptance_casebook_origin import _add, _notarize
from test_acceptance_casebook_replay_runner import CASE, NO_ARGS, _fake_runner, _stub_git

from experimental.acceptance_casebook.origin import read_origin
from experimental.acceptance_casebook.replay_runner import RunnerFailure

pytest_plugins = ["test_acceptance_casebook_origin"]


def _real_refusing_core(request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, response = fake_core_verify(request)
    response["verification"] = {
        "status": "FAILED_VERIFICATION",
        "integrity": "VALID",
        "reason_codes": ["VERIFIER_FAILED", "pytest-0"],
    }
    return status, response


def test_real_core_refusal_row_is_a_refused_origin(lg: dict[str, str], repo: Path) -> None:
    cand = _notarize(
        Path(lg["db"]), repo, "a3", _real_refusing_core, {"src/real_751.py": "z = 1\n"}
    )
    rec, origin = read_origin(Path(lg["db"]), "task-1", "a3")
    assert (rec.status, rec.integrity, rec.http_status) == ("FAILED_VERIFICATION", "VALID", 200)
    assert origin["refused"] is True and origin["accepted"] is False
    assert _add(lg, cand, "a3", "refused_good") == 0
    assert _add(lg, cand, "a3", "accepted_bad", version="v2") == 2  # still not an accepted row


@pytest.mark.parametrize("core", ["FAILED", "FAILED_VERIFICATION"])
def test_runner_maps_either_core_refusal_status_to_refused(tmp_path, monkeypatch, core) -> None:
    payload = {"status": "FAILED", "verified": False, "core_status": core}
    runner = _fake_runner(tmp_path, f"printf '%s' {json.dumps(json.dumps(payload))}\nexit 2\n")
    _stub_git(monkeypatch)
    result = runner.run(CASE, NO_ARGS)
    assert (result.verdict, result.exit_code) == ("refused", 2)


@pytest.mark.parametrize("core", ["FAILED_VERIFICATION", "FAILED"])
def test_runner_refusal_status_never_turns_a_pass_into_a_verdict(tmp_path, monkeypatch, core):
    payload = {"status": "VERIFIED", "verified": True, "core_status": core}
    runner = _fake_runner(tmp_path, f"printf '%s' {json.dumps(json.dumps(payload))}\nexit 0\n")
    _stub_git(monkeypatch)
    with pytest.raises(RunnerFailure) as caught:
        runner.run(CASE, NO_ARGS)
    assert caught.value.reason == "NO_CORE_VERDICT"
