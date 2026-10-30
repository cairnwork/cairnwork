"""#449 (items 1 and 2): offline verify and ``verify --json`` answer with a failure code for
external input, never an exception.

A JSON number such as ``1e400`` is valid JSON but parses to ``inf``, which no strict-JSON
serializer writes back (``record._digest`` uses ``allow_nan=False`` and raised ``ValueError`` out of
``verify_bundle``). The strict parser refuses it, as it refuses ``Infinity``, so a signed statement
with one is ``STATEMENT_SHAPE`` for v0.1 and v0.2 alike. ``verify --json`` re-reads the bundle for
its facts after ``verify_bundle`` has refused it; a file nested too deeply reports null facts with
the refusal (the #444 guard), needing no key.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest.bundle import BundleError, read_bundle, write_bundle
from cairnwork_attest.cli import main
from cairnwork_attest.dsse import _sign_bytes
from cairnwork_attest.keys import load_private_key
from cairnwork_attest.record import parse_strict_json
from cairnwork_attest.verify import verify_bundle
from tests.attest.test_binding_blackbox_checks import _world, check
from tests.attest.test_verify import setup_verified_bundle_fixture

MARK = "number-goes-here"  # replaced by the raw number in the signed text
OVERFLOWS = ["1e400", "-1e999", "1.5e309"]


def _signed_with(state: Path, bundle: Path, out: Path, number: str) -> Path:
    """The statement with ``number`` in the request's contract, signed as raw bytes (json.dumps
    cannot write an overflowing number)."""
    stmt = json.loads(base64.b64decode(read_bundle(bundle)["payload"]))
    stmt["predicate"]["request"]["acceptance_contract"]["note"] = MARK
    raw = json.dumps(stmt).replace(f'"{MARK}"', number)
    write_bundle(out, _sign_bytes(raw.encode(), load_private_key(state)))
    return out


@pytest.mark.parametrize("number", OVERFLOWS)
def test_the_strict_parser_refuses_a_number_that_overflows(number):
    with pytest.raises(BundleError) as caught:
        parse_strict_json(
            f'{{"a":[{number}]}}', error_cls=BundleError, constant_code="STATEMENT_SHAPE"
        )
    assert caught.value.code == "STATEMENT_SHAPE"


def test_the_strict_parser_keeps_finite_numbers():
    """Control: the largest double, an underflow to zero and plain numbers still parse."""
    assert parse_strict_json('[1.7976931348623157e308, 1e-400, -0.5, 3]') == [
        1.7976931348623157e308, 0.0, -0.5, 3,
    ]  # fmt: skip


@pytest.mark.parametrize("number", OVERFLOWS)
def test_an_overflowing_number_in_a_signed_receipt_refuses_v01(tmp_path, repo, number):
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)
    bundle = _signed_with(state, bundle, tmp_path / "inf.json", number)
    out = verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == "STATEMENT_SHAPE", out


def test_an_overflowing_number_in_a_signed_receipt_refuses_v02(tmp_path):
    w = _world(tmp_path, ("pytest-0",), blackbox_commitment="")
    out = check(w, None, _signed_with(w["state"], w["bundle"], tmp_path / "inf.json", "1e400"))
    assert out.code == "STATEMENT_SHAPE", out


def test_a_finite_number_in_the_same_place_is_only_a_hash_mismatch(tmp_path, repo):
    """Control: the substitution alone reaches the request-hash step (the hash is not redone)."""
    state = tmp_path / "state"
    bundle, pub, _base, candidate = setup_verified_bundle_fixture(state, repo)
    bundle = _signed_with(state, bundle, tmp_path / "finite.json", "1e300")
    out = verify_bundle(bundle, public_key_pem=pub, repo=repo, expected_candidate=candidate)
    assert out.code == "REQUEST_HASH_MISMATCH", out


DEPTH = 200_000  # past every supported Python's JSON decoder limit
DEEP = "[" * DEPTH + "]" * DEPTH


def _verify_json(repo: Path, pub: Path, candidate: str, bundle: Path, capsys: Any) -> dict:
    argv = ["verify", str(bundle), "--public-key", str(pub), "--repo", str(repo)]
    rc = main([*argv, "--candidate", candidate, "--json"])
    line = json.loads(capsys.readouterr().out)
    assert rc == 1, line
    assert line["ok"] is False
    assert line["statement_sha256"] is None and line["keyid"] is None, line
    return line


def test_verify_json_on_a_deeply_nested_file_reports_its_code(tmp_path, repo, capsys):
    _bundle, pub, _base, candidate = setup_verified_bundle_fixture(tmp_path / "state", repo)
    bundle = tmp_path / "deep.json"
    bundle.write_text('{"payload":"","x":' + DEEP + "}")
    assert _verify_json(repo, pub, candidate, bundle, capsys)["code"] == "BUNDLE_MALFORMED"


def test_verify_json_on_a_deeply_nested_unsigned_payload_reports_its_code(tmp_path, repo, capsys):
    """The envelope parses; its payload, never signed by the key, is the deep value."""
    _bundle, pub, _base, candidate = setup_verified_bundle_fixture(tmp_path / "state", repo)
    payload = base64.b64encode(DEEP.encode()).decode()
    envelope = {
        "payloadType": "application/vnd.in-toto+json",
        "payload": payload,
        "signatures": [{"keyid": "", "sig": "AAAA"}],
    }
    bundle = tmp_path / "deep.json"
    bundle.write_text(json.dumps(envelope))
    assert _verify_json(repo, pub, candidate, bundle, capsys)["code"] == "SIGNATURE_INVALID"
