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
    assert granted == {"contents: read", "issues: write", "actions: read"}
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
    assert "if: always()" in TEXT and '"entrypoint smoke failed"' in TEXT
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


def test_report_job_needs_and_condition() -> None:
    report = TEXT.split("\n  report:\n", 1)[1]
    assert "needs: [walkthrough, verify-action]" in report
    assert (
        "if: always() && (contains(needs.*.result, 'failure') "
        "|| contains(needs.*.result, 'cancelled'))" in report
    )
    assert "actions: read" in report
    assert TEXT.count("actions: read") == 1  # only the report job


def test_action_sha_equals_pinned_env() -> None:
    env_sha = re.search(r'VERIFY_ACTION_SHA: "([0-9a-f]{40})"', TEXT)
    assert env_sha
    used = re.findall(r"uses: cairnwork/verify-action@(\S+)", TEXT)
    assert used == [env_sha.group(1)]


def test_readme_has_the_install_command_the_workflow_runs() -> None:
    cmd = "uv tool install --from git+https://github.com/cairnwork/cairnwork.git cairnwork"
    assert cmd in TEXT
    assert cmd in (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_report_lookup_cannot_drop_or_duplicate_the_issue() -> None:
    """#330: past the search's default 30 hits, and when the lookup itself fails."""
    lookup = TEXT.split('n="$(gh issue list', 1)[1].split("\n          if ", 1)[0]
    assert "--limit 100" in lookup and lookup.rstrip().endswith('|| true)"')


def test_the_site_key_is_checked_as_ed25519_before_its_keyid() -> None:
    """#330: the keyid is sha256 of the raw key in an Ed25519 SPKI, not of any key's last bytes."""
    assert '"302a300506032b6570032100"' in TEXT and "der[12:]" in TEXT and "der[-32:]" not in TEXT
