"""#444 review (F1, F4, #447): a signed value nested deeper than ``canonical_json`` can recurse
must refuse with its step's code, never raise. Every hash or canonical comparison of external input
in verify (verify.py, verify_binding.py, verify_response.py) also catches ``RecursionError``, as
parse_strict_json already does. The payload is signed as raw bytes, written by text substitution so
that this test itself never recurses. ``DEPTH`` is probed: just past where ``canonical_json``
recurses too deeply, which the strict parser still accepts on every supported Python (3.11 counts a
comprehension frame per level, 3.12+ does not), so each test asserts its exact code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest import verify as verify_module
from cairnwork_attest.bundle import read_bundle, write_bundle
from cairnwork_attest.dsse import _sign_bytes
from cairnwork_attest.keys import load_private_key
from cairnwork_attest.verify import VerifyResult, verify_bundle
from cairnwork_protocol import canonical_json
from tests.attest.binding_helpers import policy, receiver
from tests.attest.test_binding_blackbox_checks import _world, check
from tests.attest.test_verify import setup_verified_bundle_fixture


def _probed_depth() -> int:
    """The least depth, in steps of 25, at which canonical_json recurses too deeply from here,
    plus 25: verify calls it from deeper in the stack, so there it fails at least as soon."""
    for depth in range(25, sys.getrecursionlimit() * 2, 25):
        try:
            canonical_json(json.loads("[" * depth + "]" * depth))
        except RecursionError:
            return depth + 25
    raise AssertionError("canonical_json never recursed too deeply")


DEPTH = _probed_depth()
DEEP = "[" * DEPTH + "]" * DEPTH
MARK = "deep-value-goes-here"  # replaced by DEEP in the signed text
Edit = Callable[[dict[str, Any]], None]  # edits the statement's predicate, writing MARK


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).replace(f'"{MARK}"', DEEP)


def _deep(state: Path, bundle: Path, out: Path, edit: Edit) -> Path:
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    edit(stmt["predicate"])
    request = _text(stmt["predicate"]["request"]).encode()  # record._digest's form
    stmt["predicate"]["request_hash"] = "sha256:" + hashlib.sha256(request).hexdigest()
    write_bundle(out, _sign_bytes(_text(stmt).encode(), load_private_key(state)))
    return out


def _v01(tmp_path: Path, repo: Path, edit: Edit, pin: Any = None) -> VerifyResult:
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)
    bundle = _deep(state, bundle, tmp_path / "deep.json", edit)
    return verify_bundle(
        bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate, core_identity_pin=pin
    )


def _refused(out: VerifyResult, code: str) -> None:
    """Never ``repr(out)``: the statement in it is the deep value."""
    assert out.code == code, out.code


def _copy(predicate: dict[str, Any]) -> None:
    predicate["verifier_observations"] = [MARK]


def _extra_field(predicate: dict[str, Any]) -> None:
    """A field Core's evidence hash leaves out, in the request's observation and in its copy."""
    for observations in (
        predicate["request"]["evidence_bundle"]["observations"],
        predicate["verifier_observations"],
    ):
        observations[0]["note"] = MARK


def _verifier_id(predicate: dict[str, Any]) -> None:
    """The one (PASS) observation's id is the deep value: Core's evidence hash recurses into it
    (with a second observation, sorting a list against a string would raise TypeError first)."""
    for observations in (
        predicate["request"]["evidence_bundle"]["observations"],
        predicate["verifier_observations"],
    ):
        observations[0]["verifier_id"] = MARK


def _core_identity(predicate: dict[str, Any]) -> None:
    predicate["core_identity"] = {"deep": MARK}
    predicate["response"]["core_identity"] = {"deep": MARK}


def _manifest(predicate: dict[str, Any]) -> None:
    """The one manifest entry's before_oid (#447: verify.py hashes Core's manifest first)."""
    predicate["request"]["change_manifest"]["entries"][0]["before_oid"] = MARK


CASES = {
    "manifest": (_manifest, "MANIFEST_MISMATCH"),
    "copy": (_copy, "RECEIPT_INCONSISTENT"),
    "extra_field": (_extra_field, "RECEIPT_INCONSISTENT"),
    "verifier_id": (_verifier_id, "RESPONSE_NOT_VERIFIED"),
    "core_identity": (_core_identity, "RESPONSE_NOT_VERIFIED"),
}


@pytest.mark.parametrize("case", CASES)
def test_a_deep_value_refuses_v01(tmp_path, repo, case):
    edit, code = CASES[case]
    _refused(_v01(tmp_path, repo, edit), code)


def test_a_deep_copy_refuses_v02(tmp_path):
    w = _world(tmp_path, ("pytest-0",), blackbox_commitment="")
    out = check(w, None, _deep(w["state"], w["bundle"], tmp_path / "deep.json", _copy))
    _refused(out, "RECEIPT_INCONSISTENT")


def test_a_deep_contract_field_refuses_v02(tmp_path):
    """verify_binding._chain hashes the contract to compare it with the one Core echoed."""

    def edit(predicate: dict[str, Any]) -> None:
        predicate["request"]["acceptance_contract"]["contract_id"] = MARK

    w = _world(tmp_path, ("pytest-0",), blackbox_commitment="")
    out = check(w, None, _deep(w["state"], w["bundle"], tmp_path / "deep.json", edit))
    _refused(out, "BINDING_HASH_MISMATCH")


def test_a_deep_test_path_entry_refuses_in_confidential_mode(tmp_path):
    """Confidential mode does not compare a test path's oids with anything, so the first step to
    hash Core's manifest is response_binding_problem, in the Core-verdict step."""

    def edit(predicate: dict[str, Any]) -> None:
        entries = predicate["request"]["change_manifest"]["entries"]
        next(e for e in entries if e["path"] == "tests/test_a.py")["before_oid"] = MARK

    w = _world(tmp_path, ("pytest-0",), blackbox_commitment="")
    conf = receiver(tmp_path, "conf", w["work"], w["head"])
    bundle = _deep(w["state"], w["bundle"], tmp_path / "deep.json", edit)
    pol = policy(w["state"], tmp_path, w["tests"])
    out = verify_bundle(
        bundle, public_key_pem=w["pub"], repo=conf, expected_candidate=w["head"], policy=pol
    )
    _refused(out, "RESPONSE_NOT_VERIFIED")


def test_a_deep_core_identity_pin_refuses(tmp_path, repo):
    """The caller's pin, built without recursion; the receipt itself is honest."""
    pin: dict[str, Any] = {}
    node = pin
    for _ in range(DEPTH):
        node["deep"] = {}
        node = node["deep"]
    out = _v01(tmp_path, repo, lambda predicate: None, pin=pin)
    assert out.code == "CORE_IDENTITY_MISMATCH", out.code


def test_the_text_substitution_alone_changes_nothing(tmp_path, repo):
    """Control: signed and request-hashed the same way, with nothing deep, the receipt is OK."""
    assert _v01(tmp_path, repo, lambda predicate: None).code == "OK"


def test_a_deep_value_that_only_a_message_quotes_refuses(tmp_path, repo):
    """Core's ``status`` is never hashed or compared canonically, only quoted in the refusal. On
    Python 3.14 ``repr`` recurses too deeply from about 30,700 levels while the parser accepts
    43,000, so verify_bundle's last-resort catch answers; older parsers refuse 35,000 levels
    first. STATEMENT_SHAPE either way, never an exception."""
    deep = "[" * 35_000 + "]" * 35_000

    def edit(predicate: dict[str, Any]) -> None:
        predicate["response"]["verification"]["status"] = MARK

    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    edit(stmt["predicate"])
    text = json.dumps(stmt, sort_keys=True, separators=(",", ":")).replace(f'"{MARK}"', deep)
    out_path = tmp_path / "deep.json"
    write_bundle(out_path, _sign_bytes(text.encode(), load_private_key(state)))
    out = verify_bundle(out_path, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == "STATEMENT_SHAPE", out.code


def test_any_step_that_recurses_too_deeply_refuses(tmp_path, repo, monkeypatch):
    """The last-resort catch on every Python: a step raising RecursionError is a refusal."""
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)

    def recurses(*_args: Any) -> None:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(verify_module, "_check_manifest", recurses)
    out = verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == "STATEMENT_SHAPE", out
    assert "nested too deeply" in out.detail


def test_a_step_raising_another_error_is_not_turned_into_a_refusal(tmp_path, repo, monkeypatch):
    """#444 review N3: the last-resort catch is for recursion only. A programmer error (here a
    ValueError from one step) still raises: a wider catch would hide a bug as STATEMENT_SHAPE."""
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)

    def broken(*_args: Any) -> None:
        raise ValueError("programmer error")

    monkeypatch.setattr(verify_module, "_check_manifest", broken)
    with pytest.raises(ValueError, match="programmer error"):
        verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
