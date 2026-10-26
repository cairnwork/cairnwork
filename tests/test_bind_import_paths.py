"""#292: import_paths change what the verifiers import, so the request digest and the deferred-test
binding record them when they are set; without them both stay byte-identical to before."""

import json
from pathlib import Path

import pytest
from conftest import commit_change, git
from test_deferred_tests import make_test_commit
from test_verify_receipt_e2e import check, fresh, signed
from verify_helpers import candidate, verify

from cairnwork.intake import Intake
from cairnwork.notary import default_core_identity
from cairnwork.ownership import request_digest
from cairnwork.pipeline import TaskSpec
from cairnwork_attest.keys import keyid, load_private_key

sandbox = pytest.mark.sandbox
VERIFIERS = (("pytest", ("python3", "-m", "pytest", "-q", "tests")),)
OLD_BINDING_KEYS = {
    "rule",
    "base",
    "head",
    "test_commit",
    "test_paths",
    "executed_tree",
    "verifiers",
}


def spec(**kw):
    return TaskSpec("k1", "c" * 64, "T", "do it", Path("/r"), "a" * 40, ("src/x.py",), VERIFIERS,
                    **kw)  # fmt: skip


def test_a_request_without_import_paths_is_byte_identical_to_before():
    # digests computed on main 8e1da67, before #292
    assert "import_paths" not in spec().request()
    assert request_digest(spec().request()) == (
        "sha256:49f0176b0f1d0de39ee401f74c267c02f1436a12d220fd1402e43a8cd1be2f73"
    )
    assert request_digest(spec(test_commit="b" * 40).request()) == (
        "sha256:e7d6b97e0047cbe077fa673d87401323c2f2acdcd11c35c3ff02e4fd06234f05"
    )


def test_a_request_records_the_import_paths_in_order():
    request = spec(import_paths=("src", "lib")).request()
    assert request["import_paths"] == ["src", "lib"]
    digests = {
        request_digest(spec(import_paths=paths).request())
        for paths in [(), ("src",), ("lib",), ("src", "lib"), ("lib", "src")]
    }
    assert len(digests) == 5  # each set of paths, and each order, is another request


def test_the_state_config_import_paths_reach_the_bound_request(tmp_path):
    intake = Intake(tmp_path / "state")
    intake.prepare()
    (intake.state / "requests").mkdir(exist_ok=True)
    payload = {
        "idempotency_key": "k1", "card_hash": "c" * 64, "title": "T", "instructions": "do it",
        "repo": "/r", "base_commit": "a" * 40, "allowed_files": ["src/x.py"],
        "verifiers": [["pytest", ["python3", "-m", "pytest", "-q", "tests"]]],
    }  # fmt: skip
    (intake.state / "requests" / "task-1.json").write_text(json.dumps(payload))
    (intake.state / "config.json").write_text(json.dumps({"import_paths": ["src"]}))
    with_paths = intake.load_request("task-1").request()
    assert with_paths["import_paths"] == ["src"]
    (intake.state / "config.json").write_text(json.dumps({"import_paths": []}))
    without = intake.load_request("task-1").request()
    assert "import_paths" not in without
    # a config change after binding is another request: the runner refuses it (REQUEST_MISMATCH)
    assert request_digest(with_paths) != request_digest(without)


SRC_LAYOUT = {
    "src/mypkg/__init__.py": "",
    "src/mypkg/calc.py": "def add(a, b):\n    return a + b + 1\n",
    "tests/test_calc.py": "def test_nothing():\n    pass\n",
}
REGRESSION = "from mypkg.calc import add\ndef test_regression():\n    assert add(1, 1) == 2\n"
CHECK = "python3 -m pytest -q tests/test_regression.py"
FIX = {"src/mypkg/calc.py": "def add(a, b):\n    return a + b\n"}


@pytest.fixture
def bound(repo, tmp_path):
    """A src-layout project whose deferred tests import the package only through --import-path."""
    commit_change(repo, SRC_LAYOUT)
    base, test_commit = make_test_commit(repo, {"tests/test_regression.py": REGRESSION})
    cand = candidate(repo, base, FIX)
    return repo, base, test_commit, cand


def _verify(state, project, base, cand, test_commit, *extra):
    return verify(state, project, base, cand, "--test-commit", test_commit, *extra,
                  allowed=("src/mypkg/calc.py",), tests=(CHECK,))  # fmt: skip


@sandbox
def test_verify_import_path_is_in_the_binding_and_the_receipt_holds_it(bound, state, tmp_path):
    project, base, test_commit, cand = bound
    code, out = _verify(state, project, base, cand, test_commit, "--import-path", "src")
    assert code == 0 and out["core_status"] == "VERIFIED", out
    assert out["binding"]["import_paths"] == ["src"]
    assert set(out["binding"]) == OLD_BINDING_KEYS | {"import_paths"}
    bundle, pub = signed(state, out, tmp_path, cand)
    full = fresh(tmp_path, "full", project, cand)
    git(full, "fetch", "-q", str(project.parent / "origin.git"), test_commit)
    code, result = check(bundle, pub, full, cand)
    assert code == 0 and result["ok"], result

    def pinned(paths):
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({
            "rule": "overlay-v1", "test_commit": test_commit,
            "core_identity": default_core_identity(),
            "key_id": keyid(load_private_key(state).public_key()), "import_paths": paths,
        }))  # fmt: skip
        return check(bundle, pub, full, cand, "--policy", policy)

    assert pinned(["src"])[0] == 0
    for other in ([], ["lib"], ["src", "lib"]):
        code, result = pinned(other)
        assert code == 1 and result["code"] == "IMPORT_PATHS_MISMATCH", (other, result)


@sandbox
def test_without_import_paths_the_binding_keeps_its_old_keys(project, state, tmp_path):
    from test_deferred_tests import CHECK as OLD_CHECK
    from test_deferred_tests import REGRESSION as OLD_REGRESSION
    from verify_helpers import FIX as OLD_FIX

    base, test_commit = make_test_commit(project, {"tests/test_regression.py": OLD_REGRESSION})
    cand = candidate(project, base, {"src/calc.py": OLD_FIX})
    code, out = verify(state, project, base, cand, "--test-commit", test_commit,
                       tests=(" ".join(OLD_CHECK[0][1]),))  # fmt: skip
    assert code == 0, out
    assert set(out["binding"]) == OLD_BINDING_KEYS
