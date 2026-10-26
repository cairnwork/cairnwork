"""Receiver side of #303's protection label (cairnwork/protection.py) on a v0.2 receipt.

The binding records the level only when it is not ``same_process_only`` (an absent key means that),
the binding hash covers it, and the receiver checks it against the required verifier ids in the
signed Core request before reporting it, so the label is never taken on the signer's word alone. A
receiver policy's ``protection`` is the least level it accepts: its strict mode."""

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

CHECKED = ("pytest-0", "blackbox:add")


def _world(tmp_path: Path, label: str, required: tuple[str, ...]) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p, protection=label, required=required)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    return {**p, "state": state, "bundle": bundle, "full": full, "pub": state / "public.pem",
            "tmp": tmp_path}  # fmt: skip


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return _world(tmp_path / "partial", "partial", CHECKED)


@pytest.fixture
def plain(tmp_path: Path) -> dict:
    return _world(tmp_path / "plain", "", ("pytest-0",))


def check(w: dict, bundle: Path | None = None, **kw):
    kw.setdefault("expected_candidate", w["head"])
    return verify_bundle(bundle or w["bundle"], public_key_pem=w["pub"], repo=w["full"], **kw)


def statement(w: dict) -> dict:
    return json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))


def minimum(w: dict, value) -> dict:
    return policy(w["state"], w["tmp"], w["tests"], protection=value)


def test_the_receiver_reports_the_level(world, plain):
    out = check(world)
    assert out.ok and out.assurance is not None and out.assurance["protection"] == "partial"
    assert statement(world)["predicate"]["binding"]["protection"] == "partial"
    out = check(plain)
    assert out.ok and out.assurance is not None
    assert out.assurance["protection"] == "same_process_only"
    assert "protection" not in statement(plain)["predicate"]["binding"]


def test_the_strict_gate_is_not_counted(tmp_path):
    w = _world(tmp_path / "gate", "blackbox", ("blackbox:add", "protection"))
    out = check(w)
    assert out.ok and out.assurance is not None and out.assurance["protection"] == "blackbox"


@pytest.mark.parametrize(
    "label,required",
    [("partial", ("pytest-0",)), ("", CHECKED), ("blackbox", CHECKED)],
    ids=["claims-protection", "hides-protection", "overclaims"],
)
def test_a_label_the_core_request_does_not_bear_out_is_refused(tmp_path, label, required):
    """A signer (or a producer bug) that names another level than Core's required ids give."""
    w = _world(tmp_path / "w", label, required)
    assert check(w).code == "PROTECTION_LABEL_MISMATCH"


@pytest.mark.parametrize("edit", ["drop", "blackbox"])
def test_a_label_removed_or_changed_after_the_fact_is_refused(world, tmp_path, edit):
    stmt = statement(world)
    if edit == "drop":
        del stmt["predicate"]["binding"]["protection"]
    else:
        stmt["predicate"]["binding"]["protection"] = edit
    out = resign(world["state"], stmt, tmp_path / "edited.json")  # even the signer's own key
    assert check(world, out).code == "BINDING_HASH_MISMATCH"


@pytest.mark.parametrize("bad", ["same_process_only", "", "full", "partial_linux", None, 1])
def test_a_malformed_label_is_statement_shape(world, tmp_path, bad):
    stmt = statement(world)
    stmt["predicate"]["binding"]["protection"] = bad
    out = resign(world["state"], stmt, tmp_path / "edited.json")
    assert check(world, out).code == "STATEMENT_SHAPE"


def test_a_policy_demands_a_least_level(world, plain):
    assert check(world, policy=minimum(world, "partial")).ok
    assert check(world, policy=minimum(world, "same_process_only")).ok
    assert check(world, policy=minimum(world, "blackbox")).code == "UNPROTECTED"
    assert check(plain, policy=minimum(plain, "partial")).code == "UNPROTECTED"
    assert check(plain, policy=minimum(plain, "same_process_only")).ok
    assert check(plain, policy=policy(plain["state"], plain["tmp"], plain["tests"])).ok


@pytest.mark.parametrize("bad", ["full", "", "same_process_only_macos", None, 1, ["partial"]])
def test_a_malformed_policy_level_is_refused(world, bad):
    minimum(world, bad)
    with pytest.raises(PolicyError):
        load_policy(world["tmp"] / "policy.json")


def test_the_macos_grade_is_its_own_level(tmp_path):
    """3e1d 2026-10-27 05:43: a blackbox verdict on macOS (the #316 residual) is not labelled as the
    Linux one. The ids bear out the level without the grade; a pin without ``_macos`` refuses it."""
    w = _world(tmp_path / "mac", "partial_macos", CHECKED)
    out = check(w)
    assert out.ok and out.assurance is not None and out.assurance["protection"] == "partial_macos"
    assert check(w, policy=minimum(w, "partial_macos")).ok
    assert check(w, policy=minimum(w, "partial")).code == "UNPROTECTED"
    assert check(w, policy=minimum(w, "blackbox_macos")).code == "UNPROTECTED"
    assert check(w, policy=minimum(w, "same_process_only")).ok
    lying = _world(tmp_path / "lie", "blackbox_macos", CHECKED)
    assert check(lying).code == "PROTECTION_LABEL_MISMATCH"


def test_the_linux_grade_meets_a_macos_minimum(world):
    assert check(world, policy=minimum(world, "partial_macos")).ok
    assert check(world, policy=minimum(world, "blackbox_macos")).code == "UNPROTECTED"
