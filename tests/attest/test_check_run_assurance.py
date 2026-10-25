"""Check run assurance reporting for v0.2 receipts (#232)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cairnwork_attest.checkrun import post_check_run
from tests.attest.binding_helpers import (
    notarize_bound,
    producer,
    receiver,
    sign_bound,
)
from tests.attest.test_verify import setup_verified_bundle_fixture


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


def test_v01_bundle_has_no_assurance(tmp_path: Path, repo: Path) -> None:
    # A real v0.1 bundle, signed by the key that is passed as --public-key, so the check run
    # reaches the v0.1 verified path (issue #280); a v0.1 receipt must not show an Assurance row.
    bundle, pub, _base, _cand = setup_verified_bundle_fixture(tmp_path / "state01", repo)
    http = FakeHttp()
    res = post_check_run(
        bundle,
        "octo/repo",
        public_key=pub,
        repo=repo,
        token="ghs_TOKEN",
        http=http,
    )
    assert res.ok
    summary = json.loads(http.calls[0][3])["output"]["summary"]
    assert "- Offline verification: VERIFIED" in summary
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
