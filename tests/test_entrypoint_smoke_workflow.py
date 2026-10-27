"""Pin the safety properties of the weekly entry-point smoke workflow (docs/ENTRYPOINTS.md)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "entrypoint-smoke.yml").read_text(encoding="utf-8")
DOC = (ROOT / "docs" / "ENTRYPOINTS.md").read_text(encoding="utf-8")


def test_triggers_are_schedule_and_dispatch_only() -> None:
    on_block = TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "schedule:" in on_block and "workflow_dispatch:" in on_block
    assert "pull_request" not in on_block and "push:" not in on_block


def test_no_secrets_and_no_pr_controlled_input() -> None:
    assert "secrets." not in TEXT
    assert "pull_request_target" not in TEXT
    assert "github.event." not in TEXT and "github.head_ref" not in TEXT


def test_permissions_are_minimal() -> None:
    perms = re.findall(r"^\s*permissions:\n((?:\s+[a-z-]+: \w+\n)+)", TEXT, re.MULTILINE)
    granted = {line.strip() for block in perms for line in block.splitlines()}
    assert granted == {"contents: read", "issues: write"}
    assert TEXT.count("issues: write") == 1  # only the report job may write


def test_installs_from_public_url_never_the_checkout() -> None:
    assert "git+https://github.com/cairnwork/cairnwork.git" in TEXT
    assert "--from ." not in TEXT and "uv sync" not in TEXT
    assert "cairnwork/cairnwork" not in re.findall(r"repository: (\S+)", TEXT)


def test_third_party_refs_are_pinned() -> None:
    for ref in re.findall(r"uses: cairnwork/verify-action@(\S+)", TEXT):
        assert re.fullmatch(r"[0-9a-f]{40}", ref)
    assert re.search(r'VERIFY_ACTION_SHA: "[0-9a-f]{40}"', TEXT)


def test_report_job_runs_on_failure_and_every_entry_point_is_documented() -> None:
    assert "if: failure()" in TEXT and '"entrypoint smoke failed"' in TEXT
    for item in (
        "uv tool install --from git+https://github.com/cairnwork/cairnwork.git cairnwork",
        "cairnwork init",
        "cairnwork demo",
        "cairnwork-verify",
        "cairnwork-attest verify",
        "cairnwork/verify-action",
        "https://cairnwork.github.io/keys/",
    ):
        assert item in DOC
