"""The public key file is read once; the same bytes check the signature and give the key id."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnwork_attest.checkrun import post_check_run
from cairnwork_attest.keys import keyid, public_key_pem
from cairnwork_attest.verify import verify_bundle
from tests.attest.binding_helpers import notarize_bound, policy, producer, sign_bound
from tests.attest.test_check_run import SLUG, _bundle, _pem
from tests.attest.test_check_run_gh import _gh

pytestmark = pytest.mark.usefixtures("production_git_env")


def _count_reads(monkeypatch: pytest.MonkeyPatch, target: Path, answers: list[bytes]) -> list[int]:
    """Make reads of ``target`` return answers in turn (the last repeats); count them."""
    calls = [0]
    real = Path.read_bytes

    def fake(self: Path) -> bytes:
        if self != target:
            return real(self)
        calls[0] += 1
        return answers[min(calls[0], len(answers)) - 1]

    monkeypatch.setattr(Path, "read_bytes", fake)
    return calls


def test_policy_pin_is_checked_against_the_key_that_verified(tmp_path, monkeypatch):
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    pub = state / "public.pem"
    first = pub.read_bytes()
    other = Ed25519PrivateKey.generate().public_key()
    second = public_key_pem(other).encode()
    pol = policy(state, tmp_path, p["tests"], key_id=keyid(other))
    calls = _count_reads(monkeypatch, pub, [first, second])
    res = verify_bundle(
        bundle, public_key_pem=pub, repo=p["work"], expected_candidate=p["head"], policy=pol
    )
    assert calls[0] == 1
    assert not res.ok
    assert res.code == "SIGNATURE_INVALID"


def test_signature_only_check_run_reads_the_key_once(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    pem = _pem(tmp_path, key)
    calls = _count_reads(monkeypatch, pem, [pem.read_bytes()])
    gh = _gh(tmp_path)
    result = post_check_run(_bundle(tmp_path, key), SLUG, public_key=pem, token=None, gh=gh)
    assert result.ok
    assert calls[0] == 1
