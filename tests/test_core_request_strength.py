"""Test-strength sweep 2: direct pins for core_request.build_core_request.

The builder is only exercised end to end today; these pin each field it hands to Core.
"""

import pytest

from cairnwork.changeset import GitChangeSet, ManifestEntry
from cairnwork.core_request import (
    AcceptanceSpec,
    VerifierObservation,
    build_core_request,
    change_manifest,
)

BASE_TREE, CAND_TREE = "1" * 40, "2" * 40
BASE_C, CAND_C = "a" * 40, "b" * 40


def _change(tree_target=False):
    entries = (
        ManifestEntry("a.py", "MODIFY", "3" * 40, "4" * 40, "100644", "100644"),
        ManifestEntry("b.py", "ADD", None, "5" * 40, None, "100644"),
    )
    return GitChangeSet(BASE_C, CAND_C, BASE_TREE, CAND_TREE, entries, tree_target)


def _spec(allow_deletions=None):
    kw = {} if allow_deletions is None else {"allow_deletions": allow_deletions}
    return AcceptanceSpec("c1", "sha256:" + "9" * 64, ("v2", "v1"), ("z/", "a/"), **kw)


OBS = (
    VerifierObservation("v1", "art-1", "sha256:" + "6" * 64, True),
    VerifierObservation("v2", "art-2", "sha256:" + "7" * 64, False),
    VerifierObservation("v3", "art-3", "sha256:" + "8" * 64, True),
)


def test_manifest_names_base_as_source_and_candidate_as_target():
    m = change_manifest(_change())
    assert m["source_tree"] == f"git-tree:{BASE_TREE}"
    assert m["target_tree"] == f"git-tree:{CAND_TREE}"
    assert [e["path"] for e in m["entries"]] == ["a.py", "b.py"]


def test_deletions_forbidden_by_default():
    req = build_core_request(_spec(), _change(), (), attempt_id="t1")
    assert req["acceptance_contract"]["deletion_policy"] == "FORBID"
    assert AcceptanceSpec("c", "h", (), ()).allow_deletions is False
    on = build_core_request(_spec(True), _change(), (), attempt_id="t1")
    assert on["acceptance_contract"]["deletion_policy"] == "ALLOW"


def test_every_observation_reaches_core_with_its_own_ids_and_status():
    req = build_core_request(_spec(), _change(), OBS, attempt_id="t1")
    rows = req["evidence_bundle"]["observations"]
    assert rows == [
        {
            "verifier_id": o.verifier_id,
            "artifact_id": o.artifact_id,
            "artifact_hash": o.artifact_hash,
            "status": "PASS" if o.passed else "FAIL",
        }
        for o in OBS
    ]


@pytest.mark.parametrize("tree_target", [False, True])
def test_revisions_and_ids(tree_target):
    req = build_core_request(_spec(), _change(tree_target), OBS, attempt_id="t1")
    cs = req["change_set"]
    assert cs["source_revision"] == f"git-commit:{BASE_C}"
    assert cs["target_revision"] == (
        f"git-tree:{CAND_TREE}" if tree_target else f"git-commit:{CAND_C}"
    )
    assert cs["change_set_id"] == "c1/t1"
    assert req["verification_plan"]["plan_id"] == "c1/t1/plan"
    assert req["evidence_bundle"]["bundle_id"] == "c1/t1/evidence"
    assert req["acceptance_contract"]["required_verifier_ids"] == ["v1", "v2"]
    assert req["acceptance_contract"]["allowed_paths"] == ["a/", "z/"]
