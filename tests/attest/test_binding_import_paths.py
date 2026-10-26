"""Receiver side of #292: a v0.2 receipt whose binding records the import paths the verifiers ran
with (an optional binding key, binding_extras). The binding hash covers them, and a receiver
policy can pin them."""

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


def _world(tmp_path: Path, import_paths: tuple[str, ...]) -> dict:
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p, import_paths=import_paths)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    conf = receiver(tmp_path, "conf", p["work"], p["head"])
    return {**p, "state": state, "bundle": bundle, "full": full, "conf": conf,
            "pub": state / "public.pem", "tmp": tmp_path}  # fmt: skip


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return _world(tmp_path, ("src", "lib"))


@pytest.fixture
def plain(tmp_path: Path) -> dict:
    return _world(tmp_path, ())


def check(w: dict, bundle: Path | None = None, repo: str = "full", **kw):
    kw.setdefault("expected_candidate", w["head"])
    return verify_bundle(bundle or w["bundle"], public_key_pem=w["pub"], repo=w[repo], **kw)


def statement(w: dict) -> dict:
    return json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))


def test_a_receipt_with_import_paths_verifies_and_names_them(world):
    result = check(world)
    assert result.ok, result
    assert statement(world)["predicate"]["binding"]["import_paths"] == ["src", "lib"]


def test_a_receipt_without_import_paths_keeps_the_old_binding_keys(plain):
    assert check(plain).ok
    assert "import_paths" not in statement(plain)["predicate"]["binding"]


@pytest.mark.parametrize("edit", ["drop", ["src"], ["lib", "src"], ["src", "lib", "x"]])
def test_import_paths_removed_or_changed_after_the_fact_are_refused(world, tmp_path, edit):
    stmt = statement(world)
    if edit == "drop":
        del stmt["predicate"]["binding"]["import_paths"]
    else:
        stmt["predicate"]["binding"]["import_paths"] = edit
    out = resign(world["state"], stmt, tmp_path / "edited.json")  # even the signer's own key
    assert check(world, out).code == "BINDING_HASH_MISMATCH"


def test_import_paths_added_to_a_plain_receipt_are_refused(plain, tmp_path):
    stmt = statement(plain)
    stmt["predicate"]["binding"]["import_paths"] = ["src"]
    out = resign(plain["state"], stmt, tmp_path / "edited.json")
    assert check(plain, out).code == "BINDING_HASH_MISMATCH"


@pytest.mark.parametrize(
    "bad", [[], "src", ["src", "src"], [1], ["/abs"], [""], None, [f"d{i}" for i in range(9)]]
)
def test_a_malformed_import_paths_value_is_statement_shape(world, tmp_path, bad):
    stmt = statement(world)
    stmt["predicate"]["binding"]["import_paths"] = bad
    out = resign(world["state"], stmt, tmp_path / "edited.json")
    assert check(world, out).code == "STATEMENT_SHAPE"


@pytest.mark.parametrize("repo", ["full", "conf"])
def test_a_policy_pins_the_import_paths(world, repo):
    same = policy(world["state"], world["tmp"], world["tests"], import_paths=["src", "lib"])
    assert check(world, repo=repo, policy=same).ok
    for other in ([], ["src"], ["lib", "src"], ["src", "lib", "x"]):
        pol = policy(world["state"], world["tmp"], world["tests"], import_paths=other)
        assert check(world, repo=repo, policy=pol).code == "IMPORT_PATHS_MISMATCH", other


def test_a_policy_pinning_import_paths_refuses_a_plain_receipt_and_accepts_an_empty_pin(plain):
    pol = policy(plain["state"], plain["tmp"], plain["tests"], import_paths=["src"])
    assert check(plain, policy=pol).code == "IMPORT_PATHS_MISMATCH"
    pol = policy(plain["state"], plain["tmp"], plain["tests"], import_paths=[])
    assert check(plain, policy=pol).ok


def test_a_policy_without_import_paths_does_not_check_them(world):
    pol = policy(world["state"], world["tmp"], world["tests"])
    assert check(world, policy=pol).ok


@pytest.mark.parametrize("bad", ["src", ["src", "src"], [1], None, ["/abs"], [""]])
def test_a_malformed_policy_import_paths_is_refused(world, bad):
    policy(world["state"], world["tmp"], world["tests"], import_paths=bad)
    with pytest.raises(PolicyError):
        load_policy(world["tmp"] / "policy.json")
