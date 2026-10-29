"""#444 review (F1, F4): a signed value nested deeper than the recursion limit must refuse, never
raise. ``canonical_json`` recurses, so every comparison through it in verify_response also catches
``RecursionError`` (as parse_strict_json already does). The payload is signed as raw bytes, written
by text substitution so that this test itself never recurses; a parser that refuses the depth
first (``STATEMENT_SHAPE``) is a refusal too.
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

from cairnwork_attest.bundle import read_bundle, write_bundle
from cairnwork_attest.dsse import _sign_bytes
from cairnwork_attest.keys import load_private_key
from cairnwork_attest.verify import VerifyResult, verify_bundle
from tests.attest.test_binding_blackbox_checks import _world, check
from tests.attest.test_verify import setup_verified_bundle_fixture

DEPTH = sys.getrecursionlimit() + 100
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
    assert out.code in (code, "STATEMENT_SHAPE"), out.code


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


CASES = {
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
    _refused(check(w, None, _deep(w["state"], w["bundle"], tmp_path / "deep.json", _copy)),
             "RECEIPT_INCONSISTENT")  # fmt: skip


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
