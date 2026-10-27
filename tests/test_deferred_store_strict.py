"""Strict bundle mode (``test_source: "bundle"``) accepts only a commit the private store
holds (#189): a test commit that is already in the mirror but was never imported reached the
machine by another route, so it is refused instead of being recorded as "bundle"."""

import json
import os

import pytest
from conftest import git
from test_deferred_store import make_bundle, used_dir
from test_deferred_store import store as store  # noqa: F401 - fixture
from test_deferred_tests import CHECK, REGRESSION, make_test_commit
from test_deferred_tests import project as project  # noqa: F401 - fixture
from verify_helpers import FIX, candidate, verify

from cairnwork.deferred_store import DeferredStore
from cairnwork.deferred_tests import TestCommitRejected, ensure_test_commit

TESTS = {"tests/test_regression.py": REGRESSION}
sandbox = pytest.mark.sandbox
VERIFIER = " ".join(CHECK[0][1])


def _config(state, **extra):
    (state / "config.json").write_text(json.dumps({"verifier_account": False, **extra}))


def _present_in_mirror(project, bundle, sha):
    git(project, "fetch", "-q", str(bundle), "refs/heads/t")  # e.g. a manual fetch or push
    assert git(project, "cat-file", "-t", sha) == "commit"


def test_strict_mode_refuses_a_commit_already_in_the_repo_but_not_imported(  # noqa: F811
    project, store, tmp_path
):
    _base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    _present_in_mirror(project, bundle, sha)
    strict = DeferredStore(store.path, required=True)
    with pytest.raises(TestCommitRejected) as err:
        ensure_test_commit(project, sha, strict)
    assert err.value.code == "TEST_COMMIT_NOT_IMPORTED"


def test_auto_mode_still_accepts_a_commit_already_in_the_repo(project, store, tmp_path):  # noqa: F811
    _base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    _present_in_mirror(project, bundle, sha)
    ensure_test_commit(project, sha, store)  # not required: presence is enough, as before


def test_strict_mode_accepts_an_imported_commit_already_in_the_repo(  # noqa: F811
    project, store, tmp_path
):
    _base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    store.import_bundle(project, bundle, used_dir(store))
    _present_in_mirror(project, bundle, sha)
    ensure_test_commit(project, sha, DeferredStore(store.path, required=True))
    assert git(project, "cat-file", "-t", sha) == "commit"


def test_strict_mode_checks_the_store_dir_even_when_the_commit_is_present(  # noqa: F811
    project, store, tmp_path
):
    _base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    store.import_bundle(project, bundle, used_dir(store))
    _present_in_mirror(project, bundle, sha)
    os.chmod(store.path, 0o750)
    try:
        with pytest.raises(TestCommitRejected) as err:
            ensure_test_commit(project, sha, DeferredStore(store.path, required=True))
    finally:
        os.chmod(store.path, 0o700)
    assert err.value.code == "TEST_STORE_UNSAFE"


@sandbox
def test_strict_mode_refuses_an_unimported_commit_before_burning_it(project, state, tmp_path):
    base, sha, _bundle = make_bundle(project, TESTS, tmp_path)
    _config(state, test_source="bundle")
    store = DeferredStore.for_state(state, "bundle")
    store.path.mkdir(parents=True, mode=0o700, exist_ok=True)
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    assert code == 2, out
    assert out["status"] == "REFUSED"
    assert "TEST_COMMIT_NOT_IMPORTED" in out["reason"]
    assert out["tests_consumed"] is False
    assert not (state / "verify-used-tests" / sha).exists()


@sandbox
def test_strict_mode_refuses_an_unsafe_store_before_burning_the_commit(project, state, tmp_path):
    # #337: the store check used to come from fetch_into, after the commit was burned.
    base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    _config(state, test_source="bundle")
    store = DeferredStore.for_state(state, "bundle")
    store.path.mkdir(parents=True, mode=0o700, exist_ok=True)
    store.import_bundle(project, bundle, state / "verify-used-tests")
    os.chmod(store.path, 0o750)
    cand = candidate(project, base, {"src/calc.py": FIX})
    try:
        code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    finally:
        os.chmod(store.path, 0o700)
    assert code == 2, out
    assert out["status"] == "REFUSED"
    assert "TEST_STORE_UNSAFE" in out["reason"]
    assert out["tests_consumed"] is False
    assert not (state / "verify-used-tests" / sha).exists()


@sandbox
def test_auto_mode_refuses_an_unsafe_store_holding_the_commit_before_burning(  # noqa: F811
    project, state, tmp_path
):
    # #339: the mirror lacks the commit and the store holds it, so it would come from the store.
    base, sha, bundle = make_bundle(project, TESTS, tmp_path)
    _config(state, test_source="auto")
    store = DeferredStore.for_state(state, "auto")
    store.path.mkdir(parents=True, mode=0o700, exist_ok=True)
    store.import_bundle(project, bundle, state / "verify-used-tests")
    os.chmod(store.path, 0o750)
    cand = candidate(project, base, {"src/calc.py": FIX})
    try:
        code, out = verify(state, project, base, cand, "--test-commit", sha, tests=(VERIFIER,))
    finally:
        os.chmod(store.path, 0o700)
    assert code == 2, out
    assert out["status"] == "REFUSED"
    assert "TEST_STORE_UNSAFE" in out["reason"]
    assert out["tests_consumed"] is False
    assert not (state / "verify-used-tests" / sha).exists()


@sandbox
def test_auto_mode_ignores_an_unsafe_store_without_the_commit(project, state):
    # The commit comes from origin, not the store: the store's mode does not matter, as before.
    base, test_commit = make_test_commit(project, TESTS)
    _config(state, test_source="auto")
    store = DeferredStore.for_state(state, "auto")
    store.path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(store.path, 0o750)
    cand = candidate(project, base, {"src/calc.py": FIX})
    try:
        code, out = verify(
            state, project, base, cand, "--test-commit", test_commit, tests=(VERIFIER,)
        )
    finally:
        os.chmod(store.path, 0o700)
    assert code == 0, out
    assert out["tests_consumed"] is True


@sandbox
def test_auto_mode_still_burns_and_fetches_an_unimported_commit(project, state):
    base, test_commit = make_test_commit(project, TESTS)
    _config(state, test_source="auto")
    cand = candidate(project, base, {"src/calc.py": FIX})
    code, out = verify(state, project, base, cand, "--test-commit", test_commit, tests=(VERIFIER,))
    assert code == 0, out
    assert out["tests_consumed"] is True
