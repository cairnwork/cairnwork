"""A value nested deeper than ``json``/``canonical_json`` can recurse refuses with the step's own
code instead of raising ``RecursionError`` (11/06 post-merge re-verification of #896, four places
that caught only TypeError/ValueError, five cases): writing a bundle, reading a check-run reply,
the notary's Core call, and its readback of a stored identity and of a pin."""

from __future__ import annotations

import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cairnwork.notary import CORE_ERROR, IDENTITY_MISMATCH, Notary
from cairnwork_attest.bundle import BundleError, write_bundle
from tests.attest.test_check_run import FakeHttp, _bundle, _post
from tests.test_notary import head_of, notarize

DEPTH = 100_000  # past every recursion limit on 3.11-3.13, for the C json coder and Python alike


def _deep() -> list:
    value: list = []
    for _ in range(DEPTH):
        value = [value]
    return value


DEEP_TEXT = "[" * DEPTH + "]" * DEPTH


def test_writing_a_too_deep_envelope_is_bundle_malformed(tmp_path):
    with pytest.raises(BundleError) as caught:
        write_bundle(tmp_path / "b.json", {"payload": _deep()})
    assert caught.value.code == "BUNDLE_MALFORMED"
    assert not (tmp_path / "b.json").exists()


def test_a_too_deep_created_reply_keeps_the_run_created_without_an_id(tmp_path):
    http = FakeHttp()
    http.body = '{"id": ' + DEEP_TEXT + "}"
    res = _post(_bundle(tmp_path, Ed25519PrivateKey.generate()), http)
    assert (res.ok, res.code, res.check_run_id, res.html_url) == (True, "CREATED", None, None)


def test_a_too_deep_core_response_is_recorded_as_core_error(repo, tmp_path):
    notary = Notary(tmp_path / "n.sqlite", core_verify=lambda _r: (200, {"x": _deep()}))
    verdict = notarize(notary, repo, {"src/a.py": "x\n"})
    assert not verdict.verified
    assert verdict.status == CORE_ERROR and verdict.http_status == 0


def test_a_too_deep_stored_identity_is_an_identity_mismatch(repo, tmp_path):
    notary = Notary(tmp_path / "notary.sqlite")
    assert notarize(notary, repo, {"src/a.py": "x = 2\n"}).verified
    conn = sqlite3.connect(tmp_path / "notary.sqlite")
    conn.execute("DROP TRIGGER core_verifications_no_update")
    conn.execute("UPDATE core_verifications SET expected_identity = ?", (DEEP_TEXT,))
    conn.commit()
    conn.close()
    verdict = notary.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None
    assert not verdict.verified and verdict.reason == IDENTITY_MISMATCH


def test_a_too_deep_pin_is_an_identity_mismatch(repo, tmp_path):
    path = tmp_path / "n.sqlite"
    assert notarize(Notary(path), repo, {"src/a.py": "x = 2\n"}).verified
    pinned = Notary(path, core_identity={"core": _deep()})
    verdict = pinned.verdict("task-1", "a1", expected_candidate=head_of(repo))
    assert verdict is not None
    assert not verdict.verified and verdict.reason == IDENTITY_MISMATCH
