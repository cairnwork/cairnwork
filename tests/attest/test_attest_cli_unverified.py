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


def test_a_bad_signature_also_marks_them(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    cand, _, _, bundle = _setup_signed_bundle(tmp_path, repo, capsys)
    other = tmp_path / "other.pem"
    other.write_bytes(
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    rc = main(
        ["verify", str(bundle), "--public-key", str(other), "--repo", str(repo),
         "--candidate", cand, "--json"]
    )  # fmt: skip
    data = json.loads(capsys.readouterr().out)
    assert rc != 0 and data["ok"] is False and data["code"] != "CANDIDATE_MISMATCH"
    assert data["unverified_fields"] == FIELDS


def test_a_failure_before_the_bundle_is_read_marks_them_too(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cand, _, pub, _ = _setup_signed_bundle(tmp_path, repo, capsys)
    rc = main(
        ["verify", str(tmp_path / "missing.json"), "--public-key", str(pub), "--repo", str(repo),
         "--candidate", cand, "--json"]
    )  # fmt: skip
    data = json.loads(capsys.readouterr().out)
    assert rc != 0 and data["ok"] is False
    assert data["unverified_fields"] == FIELDS
    assert all(data[k] is None for k in FIELDS)
