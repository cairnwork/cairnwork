"""Receiver policy hardening: require_full and test_roots (#232)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_binding import PolicyError, load_policy
from tests.attest.binding_helpers import (
    notarize_bound,
    policy,
    producer,
    receiver,
    sign_bound,
)


@pytest.fixture
def world(tmp_path: Path) -> dict:
    p = producer(tmp_path)
    state = tmp_path / "state"
    run = notarize_bound(state, p)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    full = receiver(tmp_path, "full", p["work"], p["head"], p["tests"])
    conf = receiver(tmp_path, "conf", p["work"], p["head"])
    return {
        **p,
        "state": state,
        "run": run,
        "bundle": bundle,
        "full": full,
        "conf": conf,
        "pub": state / "public.pem",
        "tmp": tmp_path,
    }


def _verify(w: dict, repo: str, pol: dict) -> Any:
    return verify_bundle(
        w["bundle"],
        public_key_pem=w["pub"],
        repo=w[repo],
        expected_candidate=w["head"],
        policy=pol,
    )


def test_policy_require_full(world: dict) -> None:
    pol = policy(world["state"], world["tmp"], world["tests"], require_full=True)
    res_conf = _verify(world, "conf", pol)
    assert not res_conf.ok
    assert res_conf.code == "POLICY_FULL_REQUIRED"
    assert "the policy requires full mode and the test commit is not in this repository" in (
        res_conf.detail or ""
    )

    res_full = _verify(world, "full", pol)
    assert res_full.ok
    assert res_full.code == "OK"


def test_policy_require_full_false_or_absent(world: dict) -> None:
    pol_false = policy(world["state"], world["tmp"], world["tests"], require_full=False)
    res_false = _verify(world, "conf", pol_false)
    assert res_false.ok
    assert res_false.code == "OK"

    pol_absent = policy(world["state"], world["tmp"], world["tests"])
    res_absent = _verify(world, "conf", pol_absent)
    assert res_absent.ok
    assert res_absent.code == "OK"


def test_policy_test_roots(world: dict) -> None:
    pol_ok = policy(world["state"], world["tmp"], world["tests"], test_roots=["tests/"])
    assert _verify(world, "conf", pol_ok).ok
    assert _verify(world, "full", pol_ok).ok

    pol_multi = policy(world["state"], world["tmp"], world["tests"], test_roots=["tests/", "docs/"])
    assert _verify(world, "conf", pol_multi).ok

    pol_bad_root = policy(world["state"], world["tmp"], world["tests"], test_roots=["docs/"])
    res_bad = _verify(world, "conf", pol_bad_root)
    assert not res_bad.ok
    assert res_bad.code == "TEST_ROOT_VIOLATION"
    res_full = _verify(world, "full", pol_bad_root)
    assert res_full.code == "TEST_ROOT_VIOLATION"


@pytest.mark.parametrize(
    "bad_override",
    [
        {"test_roots": ["tests/test_a.py"]},
        {"test_roots": []},
        {"require_full": "yes"},
        {"extra": 1},
        {"require_ful": True},
        {"test_roots": ["tests/../"]},
        {"test_roots": ["a//"]},
        {"test_roots": ["./"]},
        {"test_roots": ["/x/"]},
        {"test_roots": "tests/"},
    ],
)
def test_policy_malformed(world: dict, bad_override: dict) -> None:
    pol = policy(world["state"], world["tmp"], world["tests"], **bad_override)
    res = _verify(world, "conf", pol)
    assert not res.ok
    assert res.code == "POLICY_MALFORMED"

    with pytest.raises(PolicyError) as exc_info:
        load_policy(world["tmp"] / "policy.json")
    assert exc_info.value.code == "POLICY_MALFORMED"
