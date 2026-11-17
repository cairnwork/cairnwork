from __future__ import annotations

from pathlib import Path

import pytest

import cairnwork_sandbox
from cairnwork.verifier_account import VerifierAccount
from experimental.protected_accept.corpus import ENTRIES
from experimental.protected_accept.measure import measure

pytestmark = [pytest.mark.sandbox, pytest.mark.verifier_account]


def test_small_account_measurement(tmp_path: Path) -> None:
    if cairnwork_sandbox.select() is None:
        pytest.skip("sandbox backend unavailable")
    account = VerifierAccount()
    if not account.available(account.python):
        pytest.skip("verifier account unavailable")
    subset = [
        next(e for e in ENTRIES if e["kind"] == "honest"),
        next(e for e in ENTRIES if e["cls"] == "C2"),
        next(e for e in ENTRIES if e["cls"] == "C5"),
        next(e for e in ENTRIES if e["cls"] == "C9" and e.get("timeout")),
    ]
    result = measure(subset, tmp_path / "measurement.json", account=account)
    assert result["rows"][0]["protected_status"] == "PROTECTED_PASS"
    assert all(row["protected_status"] != "PROTECTED_PASS" for row in result["rows"][1:])
    assert result["rows"][-1]["protected_status"] == "INCOMPLETE"
