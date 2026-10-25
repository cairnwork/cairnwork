"""Check run assurance reporting for v0.2 receipts (#232)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnwork_attest.checkrun import post_check_run
from cairnwork_attest.dsse import _sign_bytes
from tests.attest.binding_helpers import (
    notarize_bound,
    producer,
    receiver,
    sign_bound,
)


class FakeHttp:
    def __init__(self, status: int = 201):
        self.status = status
        self.body = json.dumps({"id": 42, "html_url": "https://github.com/octo/repo/runs/42"})
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def __call__(self, url, method, headers, body):
        self.calls.append((url, method, headers, body))
        return self.status, self.body.encode()


@pytest.fixture
def v02_world(tmp_path: Path) -> dict:
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    return {
        **p,
        "state": state,
        "run": run,
        "bundle": bundle,
        "full": full,
        "pub": state / "public.pem",
        "tmp": tmp_path,
    }


def _v01_bundle(tmp_path: Path, key: Ed25519PrivateKey, cand: str, base: str) -> Path:
    pred = {"base_commit": base, "test_source": "bundle"}
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://cairnwork.github.io/attestation/verified-change/v0.1",
        "predicate": pred,
        "subject": [{"digest": {"gitCommit": cand}, "name": f"git-commit:{cand}"}],
    }
    payload = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    path = tmp_path / "v01_bundle.json"
    path.write_text(json.dumps(_sign_bytes(payload, key)))
    return path


def test_v02_full_offline_check_run_includes_assurance(v02_world: dict) -> None:
    http = FakeHttp()
    res = post_check_run(
        v02_world["bundle"],
        "octo/repo",
        public_key=v02_world["pub"],
        repo=v02_world["full"],
        token="ghs_TOKEN",
        http=http,
    )
    assert res.ok
    assert res.code == "CREATED"
    body = json.loads(http.calls[0][3])
    summary = body["output"]["summary"]
    assert "- Offline verification: VERIFIED" in summary
    assert "- Assurance: " in summary
    assert "test_source=signer_choice" in summary
    assert "mode=full" in summary
    assert "test_contents=verified" in summary
    assert "overlay=rebuilt" in summary


def test_v01_bundle_has_no_assurance(v02_world: dict, tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    b01 = _v01_bundle(tmp_path, key, v02_world["head"], v02_world["base"])
    http = FakeHttp()
    post_check_run(
        b01,
        "octo/repo",
        public_key=v02_world["pub"],
        repo=v02_world["full"],
        token="ghs_TOKEN",
        http=http,
    )
    # v0.1 does not verify against v0.2 full repo or fails gracefully, but has no Assurance row
    summary = json.loads(http.calls[0][3])["output"]["summary"]
    assert "- Assurance:" not in summary


def test_signature_only_has_no_assurance(v02_world: dict) -> None:
    http = FakeHttp()
    res = post_check_run(
        v02_world["bundle"],
        "octo/repo",
        public_key=v02_world["pub"],
        repo=None,
        token="ghs_TOKEN",
        http=http,
    )
    assert res.ok
    summary = json.loads(http.calls[0][3])["output"]["summary"]
    assert "- Offline verification: VERIFIED, signature only" in summary
    assert "- Assurance:" not in summary


def test_tampered_statement_has_no_assurance(v02_world: dict, tmp_path: Path) -> None:
    # Tamper payload
    env = json.loads(v02_world["bundle"].read_text())
    stmt = json.loads(base64.b64decode(env["payload"]))
    stmt["predicate"]["binding"]["test_paths"] = ["tests/test_tampered.py"]
    env["payload"] = base64.b64encode(json.dumps(stmt).encode()).decode()
    bad_bundle = tmp_path / "tampered.json"
    bad_bundle.write_text(json.dumps(env))

    http = FakeHttp()
    post_check_run(
        bad_bundle,
        "octo/repo",
        public_key=v02_world["pub"],
        repo=v02_world["full"],
        token="ghs_TOKEN",
        http=http,
    )
    summary = json.loads(http.calls[0][3])["output"]["summary"]
    assert "SIGNATURE_INVALID" in summary
    assert "- Assurance:" not in summary
