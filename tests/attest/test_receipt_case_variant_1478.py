"""#1478: a full-mode receiver re-derives the test scope, so an old receipt whose test commit
carries case-only variants (signed on a case-sensitive checkout before #1375) is now refused."""

from __future__ import annotations

from tests.attest.binding_helpers import git, notarize_bound, policy, producer, receiver, sign_bound
from tests.attest.test_binding_deleted_tests import check

BODY = "def test_x():\n    pass\n"


def _commit_variants(work, parent: str, names: tuple[str, ...]) -> str:
    """A commit adding every name, built with plumbing so a case-folding disk keeps both."""
    git(work, "checkout", "-q", "--detach", parent)
    for name in names:
        blob = git(work, "hash-object", "-w", "--stdin", stdin=BODY)
        git(work, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    git(work, "commit", "-q", "-m", "case variants")
    sha = git(work, "rev-parse", "HEAD")
    git(work, "branch", f"b-{sha[:10]}", sha)
    return sha


def test_old_receipt_with_case_variant_tests_is_refused_by_a_full_receiver(tmp_path):
    p = producer(tmp_path)
    names = ("tests/test_a.py", "tests/Test_A.py")
    tests = _commit_variants(p["work"], p["base"], names)
    p["tests"] = tests
    state = tmp_path / "state"
    run = notarize_bound(state, p, paths=names, unchecked=True)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    w = {
        **p,
        "state": state,
        "bundle": bundle,
        "pub": state / "public.pem",
        "tmp": tmp_path,
        "full": receiver(tmp_path, "full", p["work"], p["head"], tests),
        "conf": receiver(tmp_path, "conf", p["work"], p["head"]),
    }
    pol = policy(state, tmp_path, tests, test_roots=["tests/"])
    full = check(w, policy=pol)
    assert not full.ok and full.code == "TEST_SOURCE_MISMATCH", full
    assert "only in case" in full.detail, full
