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
    assert "default branch" in workflow
    assert "data only" in workflow
    fetch = 'git -C head fetch -q --no-tags "$BASE_REPO_URL" "$BASE_SHA"'
    assert "BASE_REPO_URL" in workflow and fetch in workflow
    assert "cancelled" in workflow
    lines = workflow.splitlines()
    in_run = False
    run_indent = 0
    for line in lines:
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if in_run and indent <= run_indent and stripped and not stripped.startswith("#"):
            in_run = False
        if in_run or stripped.startswith("run:"):
            assert "${{" not in line
        if stripped.startswith("run: |"):
            in_run = True
            run_indent = indent
