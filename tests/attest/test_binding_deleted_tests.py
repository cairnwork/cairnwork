"""#798, receiver side: a v0.2 receipt whose test commit deleted a test file records it as the
optional binding key ``deleted_test_paths`` (binding_extras). The executed tree is the head with
the file removed; both receiver modes check it, and the binding hash covers it."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork.notary import NotaryInputError
from cairnwork.notary_verify import binding_extras as producer_extras
from cairnwork_attest import binding_extras
from cairnwork_attest.bundle import read_bundle
from cairnwork_attest.verify import verify_bundle
from tests.attest.binding_helpers import (
    git,
    notarize_bound,
    policy,
    producer,
    receiver,
    resign,
    sign_bound,
)

NEW = {"tests/test_new.py": "def test_new():\n    pass\n"}


def _world(tmp_path: Path) -> dict:
    p = producer(tmp_path)  # base holds tests/test_a.py
    git(p["work"], "checkout", "-q", "--detach", p["base"])
    git(p["work"], "rm", "-q", "tests/test_a.py")
    (p["work"] / "tests").mkdir(exist_ok=True)
    (p["work"] / "tests/test_new.py").write_text(NEW["tests/test_new.py"])
    git(p["work"], "add", "-A")
    git(p["work"], "commit", "-q", "-m", "tests that delete test_a")
    p["tests"] = git(p["work"], "rev-parse", "HEAD")
    git(p["work"], "branch", f"b-{p['tests'][:10]}", p["tests"])
    state = tmp_path / "state"
    run = notarize_bound(
        state, p, paths=("tests/test_new.py",), deleted=("tests/test_a.py",), unchecked=False
    )
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    return {**p, "state": state, "bundle": bundle, "pub": state / "public.pem", "tmp": tmp_path,
            "full": receiver(tmp_path, "full", p["work"], p["head"], p["tests"]),
            "conf": receiver(tmp_path, "conf", p["work"], p["head"])}  # fmt: skip


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return _world(tmp_path)


def check(w: dict, bundle: Path | None = None, repo: str = "full", **kw):
    kw.setdefault("expected_candidate", w["head"])
    return verify_bundle(bundle or w["bundle"], public_key_pem=w["pub"], repo=w[repo], **kw)


def statement(w: dict) -> dict:
    return json.loads(base64.b64decode(read_bundle(w["bundle"])["payload"]))


@pytest.mark.parametrize("mode", ["full", "conf"])
def test_a_receipt_of_a_deleting_test_commit_verifies_in_both_modes(world, mode):
    pol = policy(
        world["state"], world["tmp"], world["tests"], test_roots=["tests/"]
    )  # conf needs it
    result = check(world, repo=mode, policy=pol)
    assert result.ok, result
    binding = statement(world)["predicate"]["binding"]
    assert binding["deleted_test_paths"] == ["tests/test_a.py"]
    assert binding["test_paths"] == ["tests/test_new.py"]


@pytest.mark.parametrize("edit", ["drop", [], ["tests/test_b.py"], ["tests/test_a.py", "x"]])
def test_the_deleted_paths_removed_or_changed_after_the_fact_are_refused(world, tmp_path, edit):
    stmt = statement(world)
    if edit == "drop":
        del stmt["predicate"]["binding"]["deleted_test_paths"]
    else:
        stmt["predicate"]["binding"]["deleted_test_paths"] = edit
    out = resign(world["state"], stmt, tmp_path / "edited.json")  # even the signer's own key
    assert not check(world, out).ok
    assert not check(world, out, repo="conf").ok


def test_a_deleted_path_listed_as_a_test_path_too_is_refused(world, tmp_path):
    stmt = statement(world)
    stmt["predicate"]["binding"]["test_paths"] = ["tests/test_a.py", "tests/test_new.py"]
    out = resign(world["state"], stmt, tmp_path / "edited.json")
    assert check(world, out).code == "STATEMENT_SHAPE"


def test_the_binding_key_is_only_present_when_a_test_file_was_deleted():
    task = SimpleNamespace(verifiers=[], import_paths=(), blackbox=None)
    from cairnwork.deferred_overlay import TestFiles

    assert "deleted_test_paths" not in producer_extras(task, None)
    assert "deleted_test_paths" not in producer_extras(task, None, TestFiles(("a.py",)))
    got = producer_extras(task, None, TestFiles(("a.py",), ("tests/z.py", "tests/b.py")))
    assert got["deleted_test_paths"] == ["tests/b.py", "tests/z.py"]


@pytest.mark.parametrize("mode", ["full", "conf"])
def test_a_binding_that_names_a_deletion_the_executed_tree_lacks_is_refused(tmp_path, mode):
    """The signer says tests/test_a.py was deleted, but the tree that ran still has it: only the
    manifest check (confidential) or the rebuild (full) can notice. #816 review."""
    from tests.attest.binding_helpers import executed

    (tmp_path / "w").mkdir()
    w = _world(tmp_path / "w")
    tree, judged = executed(w["work"], w["head"], w["tests"], ("tests/test_new.py",), ())
    run = notarize_bound(
        tmp_path / "s2", w, attempt="a2", paths=("tests/test_new.py",),
        deleted=("tests/test_a.py",), tree_and_judged=(tree, judged),
    )  # fmt: skip
    bundle = sign_bound(tmp_path / "s2", run, tmp_path / "forged.json")
    pol = policy(tmp_path / "s2", tmp_path, w["tests"], test_roots=["tests/"])
    got = verify_bundle(
        bundle, public_key_pem=tmp_path / "s2/public.pem", repo=w[mode],
        expected_candidate=w["head"], policy=pol,
    )  # fmt: skip
    assert not got.ok and got.code in ("HEAD_TREE_MISMATCH", "TREE_MISMATCH"), got


def test_the_notary_refuses_a_binding_that_leaves_the_deletion_out(tmp_path):
    p = producer(tmp_path)
    git(p["work"], "checkout", "-q", "--detach", p["base"])
    git(p["work"], "rm", "-q", "tests/test_a.py")
    git(p["work"], "commit", "-q", "-m", "delete only")
    p["tests"] = git(p["work"], "rev-parse", "HEAD")
    git(p["work"], "checkout", "-q", "main")
    state = tmp_path / "state"
    with pytest.raises(
        NotaryInputError
    ):  # executed tree built with the deletion, binding says none
        from tests.attest.binding_helpers import executed

        tree, judged = executed(p["work"], p["head"], p["tests"], (), ("tests/test_a.py",))
        notarize_bound(state, p, paths=("tests/test_a.py",), tree_and_judged=(tree, judged),
                       unchecked=False)  # fmt: skip
    conn = sqlite3.connect(state / "notary.sqlite")
    assert conn.execute("SELECT count(*) FROM core_verifications").fetchone()[0] == 0


def test_confidential_mode_needs_test_roots_for_deleted_paths(world):
    """#816 review: without the pin a deleted path could be any file, e.g. src/a.py."""
    no_roots = policy(world["state"], world["tmp"], world["tests"])
    assert check(world, repo="conf", policy=no_roots).code == "TEST_ROOT_VIOLATION"
    assert check(world, repo="full", policy=no_roots).ok  # full mode reads the test commit
    other = policy(world["state"], world["tmp"], world["tests"], test_roots=["lib/"])
    assert check(world, repo="conf", policy=other).code == "TEST_ROOT_VIOLATION"


@pytest.mark.parametrize(
    ("value", "ok"),
    [(["a/b.py"], True), ([], True), (["b", "a"], False), (["a", "a"], False), (["/a"], False),
     ("a", False), ([1], False)],
)  # fmt: skip
def test_the_deleted_paths_shape(value, ok):
    problem = binding_extras.binding_problem({"deleted_test_paths": value, "test_paths": ["t.py"]})
    assert (problem == "") is (ok and value != [])  # an empty value is never recorded


@pytest.mark.parametrize("mode", ["full", "conf"])
def test_a_policy_pins_the_deleted_paths(world, mode):
    same = policy(
        world["state"],
        world["tmp"],
        world["tests"],
        test_roots=["tests/"],
        deleted_test_paths=["tests/test_a.py"],
    )
    assert check(world, repo=mode, policy=same).ok
    for other in ([], ["tests/test_b.py"]):
        pol = policy(
            world["state"],
            world["tmp"],
            world["tests"],
            test_roots=["tests/"],
            deleted_test_paths=other,
        )
        assert check(world, repo=mode, policy=pol).code == "DELETED_TEST_PATHS_MISMATCH", other


def test_the_deleted_paths_are_capped_like_the_import_path_list():
    """#870: at most 8 (the sibling ``import_paths`` limit); 9 is refused with the shape reason."""
    key = "deleted_test_paths"
    eight = sorted(f"tests/t{n}.py" for n in range(8))
    nine = sorted(f"tests/t{n}.py" for n in range(9))
    assert binding_extras.binding_problem({key: eight, "test_paths": ["t.py"]}) == ""
    got = binding_extras.binding_problem({key: nine, "test_paths": ["t.py"]})
    assert got == f"binding {key} has the wrong shape or is empty"
