"""Receiver side of #303: a v0.2 receipt whose binding records the commitment of the owner's
blackbox declaration (an optional binding key, binding_extras). The binding hash covers it, and a
receiver policy pins the value it recomputes from the declaration file."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_binding import PolicyError, load_policy
from tests.attest.binding_helpers import (
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)

COMMITMENT = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32


def _world(tmp_path: Path, commitment: str) -> dict:
    p = producer(tmp_path)
    state = tmp_path / "state"
    declared = ("blackbox:add",) if commitment else ()  # as the producer requires them (#312)
    run = notarize_bound(
        state, p, blackbox_commitment=commitment, required=("pytest-0", *declared),
        protection="partial" if declared else "",
    )  # fmt: skip
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    return {**p, "state": state, "bundle": bundle, "full": full, "pub": state / "public.pem",
            "tmp": tmp_path}  # fmt: skip


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return _world(tmp_path, COMMITMENT)


@pytest.fixture
def plain(tmp_path: Path) -> dict:
    (tmp_path / "plain").mkdir()  # its own producer, next to world's in the same test
    return _world(tmp_path / "plain", "")


def check(w: dict, bundle: Path | None = None, **kw):
    kw.setdefault("expected_candidate", w["head"])
    return verify_bundle(bundle or w["bundle"], public_key_pem=w["pub"], repo=w["full"], **kw)


def statement(w: dict) -> dict:
    return json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))


def pinned(w: dict, value) -> dict:
    return policy(w["state"], w["tmp"], w["tests"], blackbox_commitment=value)


def test_a_receipt_with_a_commitment_verifies_and_names_it(world, plain):
    assert check(world).ok
    assert statement(world)["predicate"]["binding"]["blackbox_commitment"] == COMMITMENT
    assert check(plain).ok and "blackbox_commitment" not in statement(plain)["predicate"]["binding"]


@pytest.mark.parametrize("edit", ["drop", OTHER])
def test_a_commitment_removed_or_changed_after_the_fact_is_refused(world, tmp_path, edit):
    stmt = statement(world)
    if edit == "drop":
        del stmt["predicate"]["binding"]["blackbox_commitment"]
    else:
        stmt["predicate"]["binding"]["blackbox_commitment"] = edit
    out = resign(world["state"], stmt, tmp_path / "edited.json")  # even the signer's own key
    assert check(world, out).code == "BINDING_HASH_MISMATCH"


@pytest.mark.parametrize("bad", ["", "sha256:AB", "ab" * 32, None, 1])
def test_a_malformed_commitment_is_statement_shape(world, tmp_path, bad):
    stmt = statement(world)
    stmt["predicate"]["binding"]["blackbox_commitment"] = bad
    out = resign(world["state"], stmt, tmp_path / "edited.json")
    assert check(world, out).code == "STATEMENT_SHAPE"


def test_a_policy_pins_the_commitment(world, plain):
    assert check(world, policy=pinned(world, COMMITMENT)).ok
    assert check(world, policy=pinned(world, OTHER)).code == "BLACKBOX_COMMITMENT_MISMATCH"
    assert check(world, policy=pinned(world, "")).code == "BLACKBOX_COMMITMENT_MISMATCH"
    assert check(plain, policy=pinned(plain, COMMITMENT)).code == "BLACKBOX_COMMITMENT_MISMATCH"
    assert check(plain, policy=pinned(plain, "")).ok
    assert check(world, policy=policy(world["state"], world["tmp"], world["tests"])).ok


@pytest.mark.parametrize("bad", ["sha256:xyz", None, 1, ["sha256:" + "ab" * 32]])
def test_a_malformed_policy_commitment_is_refused(world, bad):
    pinned(world, bad)
    with pytest.raises(PolicyError):
        load_policy(world["tmp"] / "policy.json")
