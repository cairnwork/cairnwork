from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci import pin_guard_status


@pytest.mark.parametrize(
    "job,rc,expected",
    [
        ("success", "0", "success"),
        ("success", "1", "unapproved"),
        ("success", "2", "error"),
        ("success", "", "error"),
        ("success", "7", "error"),
        ("failure", "0", "error"),
        ("cancelled", "0", "error"),
        ("skipped", "", "error"),
    ],
)
def test_decide(job: str, rc: str, expected: str) -> None:
    assert pin_guard_status.decide(job, rc) == expected


@pytest.mark.parametrize(
    "rc,state,description",
    [
        ("0", "success", "No unapproved guarded pin changes"),
        ("1", "failure", "Guarded pin changes need approval"),
    ],
)
def test_main_writes_github_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rc: str, state: str, description: str
) -> None:
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("PIN_GUARD_JOB_RESULT", "success")
    monkeypatch.setenv("PIN_GUARD_RC", rc)
    assert pin_guard_status.main() == 0
    assert output.read_text(encoding="utf-8") == f"state={state}\ndescription={description}\n"
