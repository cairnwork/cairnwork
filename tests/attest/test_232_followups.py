"""#232 follow-up tests for cairnwork_attest."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from cairnwork.deferred_tests import TestCommitRejected, overlay_tree
from cairnwork.notary_verify import binding_hash
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.checkrun import post_check_run
from cairnwork_attest.verify import verify_bundle
from cairnwork_attest.verify_binding import _Ctx, _test_source
from tests.attest.binding_helpers import (
    commit_on,
    git,
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)
from tests.attest.test_check_run_assurance import FakeHttp
from tests.attest.test_verify import fake_core_verify


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


def _statement(bundle: Path) -> dict:
    return json.loads(base64.b64decode(read_bundle(bundle)["payload"]))


def _liar(w: dict, stmt: dict, name: str) -> Path:
    predicate = stmt["predicate"]
    predicate["response"]["hashes"] = fake_core_verify(predicate["request"])[1]["hashes"]
    return resign(w["state"], stmt, w["tmp"] / f"{name}.json", rehash=True)


# ---------------------------------------------------------------------------
# a. test_roots prefix sibling
# ---------------------------------------------------------------------------


def test_roots_prefix_sibling(world: dict) -> None:
    evil_tests = commit_on(
        world["work"], world["base"], {"tests-evil/b.py": "def test_b(): pass\n"}, "evil tests"
    )
    run = notarize_bound(
        world["state"],
        {**world, "tests": evil_tests},
        paths=("tests-evil/b.py",),
        attempt="a2",
    )
    bundle = sign_bound(world["state"], run, world["tmp"] / "evil_bundle.json")
    pol = policy(world["state"], world["tmp"], evil_tests, test_roots=["tests/"])
    res = verify_bundle(
        bundle,
        public_key_pem=world["pub"],
        repo=world["conf"],
        expected_candidate=world["head"],
        policy=pol,
    )
    assert not res.ok
    assert res.code == "TEST_ROOT_VIOLATION"

    # Defense-in-depth: test _test_source directly with a policy root lacking
    # trailing slash ("tests"). On main (line 309 using path.startswith(r)),
    # this fails because "tests-evil/b.py".startswith("tests") is True.
    # With line 309 fixed to r.rstrip("/") + "/", it is rejected with TEST_ROOT_VIOLATION.
    ctx = _Ctx(
        repo=world["conf"],
        statement={"predicate": {"hidden_test_commit": evil_tests}},
        binding={
            "rule": "overlay-v1",
            "base": world["base"],
            "head": world["head"],
            "test_commit": evil_tests,
            "test_paths": ["tests-evil/b.py"],
        },
        expected_candidate=world["head"],
        expected_base=world["base"],
        policy={"rule": "overlay-v1", "test_commit": evil_tests, "test_roots": ["tests"]},
        full=False,
    )
    res_direct = _test_source(ctx)
    assert res_direct is not None
    assert not res_direct.ok
    assert res_direct.code == "TEST_ROOT_VIOLATION"


# ---------------------------------------------------------------------------
# b. test_roots mixed list (M2)
# ---------------------------------------------------------------------------


def test_roots_mixed_list(world: dict) -> None:
    # First path is allowed under "tests/", second path is outside allowed roots.
    # If mutation M2 checks only [:1], it checks tests/a.py and erroneously succeeds.
    ctx = _Ctx(
        repo=world["conf"],
        statement={"predicate": {"hidden_test_commit": world["tests"]}},
        binding={
            "rule": "overlay-v1",
            "base": world["base"],
            "head": world["head"],
            "test_commit": world["tests"],
            "test_paths": ["tests/a.py", "tools/b.py"],
        },
        expected_candidate=world["head"],
        expected_base=world["base"],
        policy={"rule": "overlay-v1", "test_commit": world["tests"], "test_roots": ["tests/"]},
        full=False,
    )
    res = _test_source(ctx)
    assert res is not None
    assert not res.ok
    assert res.code == "TEST_ROOT_VIOLATION"
    assert "tools/b.py" in (res.detail or "")


# ---------------------------------------------------------------------------
# c. #232 item 5 redundant checks (M3, M4, M5, M6)
# ---------------------------------------------------------------------------


def test_duplicate_manifest_path(world: dict) -> None:
    # c1: Duplicate manifest entry in confidential mode triggers HEAD_TREE_MISMATCH (M3)
    stmt = _statement(world["bundle"])
    entries = stmt["predicate"]["request"]["change_manifest"]["entries"]
    entries.append(dict(entries[0]))
    bundle = _liar(world, stmt, "dup_manifest")
    pol = policy(world["state"], world["tmp"], world["tests"])
    res = verify_bundle(
        bundle,
        public_key_pem=world["pub"],
        repo=world["conf"],
        expected_candidate=world["head"],
        policy=pol,
    )
    assert not res.ok
    assert res.code == "HEAD_TREE_MISMATCH"
    assert "Core's manifest names a path twice" in (res.detail or "")


def test_commit_parents_check(world: dict) -> None:
    # c2: Test commit with 2 parents in full mode triggers TEST_SOURCE_MISMATCH (M4)
    tree = git(world["work"], "rev-parse", f"{world['tests']}^{{tree}}")
    two_parents = git(
        world["work"],
        "commit-tree",
        tree,
        "-p",
        world["base"],
        "-p",
        world["head"],
        "-m",
        "two parents",
    )
    git(world["full"], "fetch", "-q", str(world["work"]), f"{two_parents}:refs/tags/p2")

    stmt = _statement(world["bundle"])
    stmt["predicate"]["hidden_test_commit"] = two_parents
    stmt["predicate"]["binding"]["test_commit"] = two_parents
    req = stmt["predicate"]["request"]
    req["acceptance_contract"]["requirements_hash"] = binding_hash(stmt["predicate"]["binding"])
    bundle = _liar(world, stmt, "two_parents")
    res = verify_bundle(
        bundle,
        public_key_pem=world["pub"],
        repo=world["full"],
        expected_candidate=world["head"],
    )
    assert not res.ok
    assert res.code == "TEST_SOURCE_MISMATCH"
    assert "test commit is not one commit of these paths on base" in (res.detail or "")


def test_sorted_unique_test_paths(world: dict) -> None:
    # c3: Unsorted test_paths in binding triggers STATEMENT_SHAPE (M5)
    stmt = _statement(world["bundle"])
    stmt["predicate"]["binding"]["test_paths"] = [
        "tests/z.py",
        "tests/a.py",
    ]
    bundle = resign(world["state"], stmt, world["tmp"] / "unsorted.json", rehash=False)
    res = verify_bundle(
        bundle,
        public_key_pem=world["pub"],
        repo=world["conf"],
        expected_candidate=world["head"],
    )
    assert not res.ok
    assert res.code == "STATEMENT_SHAPE"
    assert "binding values have the wrong shape" in (res.detail or "")


def test_name_not_path_overlay_tree(world: dict) -> None:
    # c4: overlay_tree when test_commit lacks a path in paths raises OVERLAY_UNSUPPORTED_ENTRY (M6)
    idx = world["tmp"] / "idx"
    with pytest.raises(TestCommitRejected) as exc_info:
        overlay_tree(
            world["full"],
            world["head"],
            world["tests"],
            ("tests/missing_file.py",),
            idx,
        )
    assert exc_info.value.code == "OVERLAY_UNSUPPORTED_ENTRY"


# ---------------------------------------------------------------------------
# d. Check Run output caveat text (M7)
# ---------------------------------------------------------------------------


def test_check_run_caveat(world: dict) -> None:
    # Full mode without policy includes Assurance row with test_source=signer_choice
    http = FakeHttp()
    res = post_check_run(
        world["bundle"],
        "octo/repo",
        public_key=world["pub"],
        repo=world["full"],
        token="ghs_TOKEN",
        http=http,
    )
    assert res.ok
    assert res.code == "CREATED"
    body = json.loads(http.calls[0][3])
    summary = body["output"]["summary"]
    assert "- Assurance:" in summary
    assert "test_source=signer_choice" in summary
