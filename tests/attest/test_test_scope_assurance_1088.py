"""The v0.2 assurance names whether the receiver re-derived test scope (#1088)."""

from tests.attest import test_verify_binding as _binding
from tests.attest.binding_helpers import policy

world = _binding.world
check = _binding.check


def test_full_receipt_without_test_roots_reports_signer_choice(world):
    result = check(world)
    assert result.ok and result.assurance is not None, result
    assert result.assurance["test_scope"] == "signer_choice"


def test_full_receipt_with_test_roots_reports_verified(world):
    pinned = policy(world["state"], world["tmp"], world["tests"], test_roots=["tests/"])
    result = check(world, policy=pinned)
    assert result.ok and result.assurance is not None, result
    assert result.assurance["test_scope"] == "verified"


def test_confidential_receipt_reports_test_scope_not_verified(world):
    pinned = policy(world["state"], world["tmp"], world["tests"])
    result = check(world, repo="conf", policy=pinned)
    assert result.ok and result.assurance is not None, result
    assert result.assurance["test_scope"] == "not_verified"


def test_assurance_preserves_every_existing_key_and_value(world):
    full = check(world)
    assert full.ok and full.assurance == {
        "mode": "full",
        "test_source": "signer_choice",
        "head": "verified",
        "non_test_paths": "verified",
        "test_contents": "verified",
        "overlay": "rebuilt",
        "verifier_runs": "signer_claim",
        "protection": "same_process_only",
        "blackbox_checks": "not_checked",
        "test_scope": "signer_choice",
    }
    confidential_policy = policy(world["state"], world["tmp"], world["tests"])
    confidential = check(world, repo="conf", policy=confidential_policy)
    assert confidential.ok and confidential.assurance == {
        "mode": "confidential",
        "test_source": "policy_pinned",
        "head": "verified",
        "non_test_paths": "verified_against_core_manifest",
        "test_contents": "not_verified",
        "overlay": "not_verified",
        "verifier_runs": "signer_claim",
        "protection": "same_process_only",
        "blackbox_checks": "not_checked",
        "test_scope": "not_verified",
    }
