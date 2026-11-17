from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_ac7_trusted_workflow_wiring_and_empty_approvers() -> None:
    workflow = (ROOT / ".github/workflows/pin-guard-trusted.yml").read_text(encoding="utf-8")
    assert "pull_request_target:" in workflow
    assert "trusted/scripts/ci/pin_guard.py" in workflow
    assert "head/scripts/ci/pin_guard.py" not in workflow
    assert "path: head" in workflow
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow
    assert 'context="pin guard (trusted)"' in workflow
    assert "permissions: {}" in workflow
    logins = [
        line.strip()
        for line in (ROOT / "scripts/ci/pin_guard_approvers.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert logins == []
