"""A required verifier id appears at most once among a signed receipt's observations (#449)."""

from __future__ import annotations

import base64
import json
import secrets
from pathlib import Path
from typing import Any

import pytest
from verify_helpers import FIX, VERIFY, base_of, candidate

from cairnwork import pipeline_judge
from cairnwork.blackbox import Case, Check, Declaration
from cairnwork.blackbox_judge import CheckVerdict
from cairnwork.core_request import VerifierObservation
from cairnwork.intake import Intake
from cairnwork.verify_candidate import verify_candidate
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.cli import main
from cairnwork_attest.keys import load_private_key
from cairnwork_attest.verify import verify_bundle
from cairnwork_protocol import evidence_bundle_hash
from tests.attest.binding_helpers import resign
from tests.attest.test_binding_blackbox_checks import _world
from tests.attest.test_verify import setup_verified_bundle_fixture

INCONSISTENT = "RECEIPT_INCONSISTENT"


def _signed_edit(state: Path, bundle: Path, out: Path, edit: Any) -> Path:
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    predicate = stmt["predicate"]
    evidence = predicate["request"]["evidence_bundle"]
    observations = evidence["observations"]
    edit(observations)
    predicate["verifier_observations"] = observations
    predicate["response"]["hashes"]["evidence_bundle_hash"] = evidence_bundle_hash(
        predicate["request"]["evidence_bundle"]
    )
    return resign(state, stmt, out, rehash=True)


def _change(kind: str):
    def edit(observations: list[dict[str, Any]]) -> None:
        match = next(o for o in observations if o.get("verifier_id") in ("pytest", "pytest-0"))
        if kind == "not_pass":
            match["status"] = "FAIL"
        elif kind == "missing":
            observations.remove(match)
        else:
            observations.append(dict(match))

    return edit


@pytest.mark.parametrize("kind", ["duplicated"])
@pytest.mark.parametrize("version", ["v0.1", "v0.2"])
def test_required_observation_must_not_repeat(tmp_path, repo, kind, version):
    if version == "v0.1":
        state = tmp_path / "state"
        bundle, pub, _base, candidate_sha = setup_verified_bundle_fixture(state, repo)
        edited = _signed_edit(state, bundle, tmp_path / "edited.json", _change(kind))
        result = verify_bundle(
            edited, public_key_pem=pub, repo=repo, expected_candidate=candidate_sha
        )
    else:
        world = _world(tmp_path, ("pytest-0",))
        edited = _signed_edit(
            world["state"], world["bundle"], tmp_path / "edited.json", _change(kind)
        )
        result = verify_bundle(
            edited,
            public_key_pem=world["pub"],
            repo=world["full"],
            expected_candidate=world["head"],
        )
    assert result.code == INCONSISTENT, result
    assert "pytest" in result.detail or "pytest-0" in result.detail
    assert "duplicated" in result.detail


def test_fixture_generated_v01_and_v02_receipts_remain_valid(tmp_path, repo):
    state = tmp_path / "state"
    bundle, pub, _base, candidate_sha = setup_verified_bundle_fixture(state, repo)
    assert verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate_sha).ok

    v02_dir = tmp_path / "v02"
    v02_dir.mkdir()
    world = _world(v02_dir, ("pytest-0",))
    assert verify_bundle(
        world["bundle"],
        public_key_pem=world["pub"],
        repo=world["full"],
        expected_candidate=world["head"],
    ).ok


@pytest.mark.parametrize("shape", ["not_list", "non_object_item"])
def test_malformed_observation_shapes_do_not_raise(shape):
    from cairnwork_attest.verify_response import _required_observations_problem

    raw: Any = (
        "not-a-list"
        if shape == "not_list"
        else [{"verifier_id": "pytest", "status": "PASS"}, "ignored"]
    )
    result = _required_observations_problem(["pytest"], {"evidence_bundle": {"observations": raw}})
    assert result is None


@pytest.mark.sandbox
def test_real_pipeline_multi_id_receipt_verifies_offline(state, project, monkeypatch, tmp_path):
    """Pipeline-generated ids/observations survive signing and offline receiver verification."""
    from cairnwork_attest.keys import public_key_pem

    declaration = Declaration(
        secrets.token_hex(32), (Check("add", "calc", "add", (Case("c1", "[2,3]", "{}", "5"),)),)
    )

    def passed(_declaration, _ctx, _commit, _run_id, **_kwargs):
        obs = VerifierObservation("blackbox:add", "x/blackbox.log#add", "sha256:" + "a" * 64, True)
        return (CheckVerdict(obs, "blackbox:add: PASS"),), None

    monkeypatch.setattr(pipeline_judge, "judge_blackbox", passed)
    base = base_of(project)
    fix = candidate(project, base, {"src/calc.py": FIX})
    out = verify_candidate(
        Intake(state),
        repo=project,
        base=base,
        candidate=fix,
        allowed_files=("src/calc.py",),
        verifiers=(("pytest-0", tuple(VERIFY.split())),),
        blackbox=declaration,
    )
    assert out["verified"] and out["core_status"] == "VERIFIED", out

    assert main(["keygen", "--state", str(state)]) == 0
    bundle = tmp_path / "pipeline.bundle.json"
    assert (
        main(
            [
                "statement",
                "--state",
                str(state),
                "--task-id",
                out["task_id"],
                "--attempt-id",
                out["attempt_id"],
                "--candidate",
                out["candidate_commit"],
                "--out",
                str(bundle),
                "--sign",
            ]
        )
        == 0
    )
    pub = state / "public.pem"
    pub.write_text(public_key_pem(load_private_key(state).public_key()), encoding="ascii")
    result = verify_bundle(
        bundle, public_key_pem=pub, repo=project, expected_candidate=out["candidate_commit"]
    )
    assert result.ok, result
    assert result.statement is not None
    request = result.statement["predicate"]["request"]
    ids = request["acceptance_contract"]["required_verifier_ids"]
    observations = request["evidence_bundle"]["observations"]
    assert isinstance(observations, list)
    assert set(ids) == {"pytest-0", "blackbox:add"}
    assert all(
        sum(o["verifier_id"] == item and o["status"] == "PASS" for o in observations) == 1
        for item in ids
    )
