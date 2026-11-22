from __future__ import annotations

import base64
import hashlib
import json
import subprocess

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnwork_attest import checkrun
from cairnwork_attest.bundle import read_bundle as original_read_bundle
from cairnwork_attest.verify import _verify_loaded as original_verify_loaded
from tests.attest.test_check_run import FakeHttp, _bundle, _pem, _post


def _summary(http: FakeHttp) -> str:
    return json.loads(http.calls[0][3])["output"]["summary"]


def _setup(tmp_path):
    key = Ed25519PrivateKey.generate()
    bundle_path = _bundle(tmp_path, key)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return key, bundle_path, repo


def test_full_check_run_reads_bundle_once(tmp_path, monkeypatch) -> None:
    key, bundle_path, repo = _setup(tmp_path)
    count = 0

    def counted(path):
        nonlocal count
        count += 1
        return original_read_bundle(path)

    monkeypatch.setattr(checkrun, "read_bundle", counted)
    response = _post(bundle_path, FakeHttp(), public_key=_pem(tmp_path, key), repo=repo)
    assert response.ok
    assert count == 1


def test_check_run_verifies_the_bundle_bytes_used_for_summary(tmp_path, monkeypatch) -> None:
    key, bundle_path, repo = _setup(tmp_path)
    original_env = original_read_bundle(bundle_path)
    original_bytes = bundle_path.read_bytes()
    payload = base64.b64decode(original_env["payload"], validate=True)
    expected_sha = hashlib.sha256(payload).hexdigest()
    replacement_dir = tmp_path / "replacement"
    replacement_dir.mkdir()
    replacement_path = _bundle(
        replacement_dir,
        key,
        {
            "base_commit": "c" * 40,
            "test_source": "origin",
        },
    )
    replacement = replacement_path.read_bytes()
    bundle_path.write_bytes(original_bytes)
    count = 0
    verified_envelopes = []

    def replace_after_read(path):
        nonlocal count
        count += 1
        envelope = original_read_bundle(path)
        if count == 1:
            bundle_path.write_bytes(replacement)
        return envelope

    monkeypatch.setattr(checkrun, "read_bundle", replace_after_read)

    def capture_verification(path, **kwargs):
        verified_envelopes.append(kwargs.get("envelope"))
        return original_verify_loaded(path, **kwargs)

    monkeypatch.setattr(checkrun, "_verify_loaded", capture_verification)
    http = FakeHttp()
    response = _post(bundle_path, http, public_key=_pem(tmp_path, key), repo=repo)
    assert response.ok
    assert count == 1
    assert verified_envelopes == [original_env]
    summary = _summary(http)
    assert expected_sha in summary
