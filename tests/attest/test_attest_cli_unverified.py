"""verify --json labels bundle-read fields as unverified on failure (#749)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairnwork_attest.cli import main
from tests.attest.test_attest_cli_json import _setup_signed_bundle

FIELDS = ["base", "core_status", "keyid", "statement_sha256", "test_source"]


def _verify(tmp_path, repo, capsys, candidate_override=None):
    cand, _, pub, bundle = _setup_signed_bundle(tmp_path, repo, capsys)
    rc = main(
        ["verify", str(bundle), "--public-key", str(pub), "--repo", str(repo),
         "--candidate", candidate_override or cand, "--json"]
    )  # fmt: skip
    return rc, json.loads(capsys.readouterr().out)


def test_failure_marks_bundle_fields_unverified(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, data = _verify(tmp_path, repo, capsys, "0" * 40)
    assert rc == 1 and data["ok"] is False
    assert data["unverified_fields"] == FIELDS
    assert data["keyid"]  # still present for downstream readers


def test_success_output_has_no_marker(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, data = _verify(tmp_path, repo, capsys)
    assert rc == 0
    assert "unverified_fields" not in data
    assert sorted(data) == [
        "assurance", "base", "candidate", "code", "core_status", "detail",
        "keyid", "ok", "statement_sha256", "test_source",
    ]  # fmt: skip
