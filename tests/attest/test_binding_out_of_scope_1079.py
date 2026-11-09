"""#1079 item 5: refuse out-of-scope added or modified test paths at the receiver."""

from __future__ import annotations

from pathlib import Path

import pytest

from cairnwork.deferred_tests import out_of_scope
from cairnwork_attest.receipt_scope import roots_problem
from tests.attest.binding_helpers import (
    commit_on,
    notarize_bound,
    policy,
    producer,
    receiver,
    sign_bound,
)
from tests.attest.test_binding_deleted_tests import check

FORBIDDEN = (
    "tests/pytest.ini",
    "tests/X.PTH",
    "tests/SiteCustomize/h.py",
    "tests/sub/PyProject.toml",
)


def _world(tmp_path: Path, paths: tuple[str, ...]) -> dict:
    p = producer(tmp_path)
    files = {"tests/test_new.py": "def test_new():\n    pass\n"}
    files.update(
        {
            path: "# deliberately synthetic binding\n"
            for path in paths
            if path != "tests/test_new.py"
        }
    )
    tests = commit_on(p["work"], p["base"], files, "synthetic test commit")
    p["tests"] = tests
    state = tmp_path / "state"
    run = notarize_bound(state, p, paths=paths, unchecked=True)
    bundle = sign_bound(state, run, tmp_path / "bundle.json")
    return {
        **p,
        "state": state,
        "bundle": bundle,
        "pub": state / "public.pem",
        "tmp": tmp_path,
        "full": receiver(tmp_path, "full", p["work"], p["head"], tests),
        "conf": receiver(tmp_path, "conf", p["work"], p["head"]),
    }


@pytest.mark.parametrize("path", FORBIDDEN)
@pytest.mark.parametrize(
    ("mode", "roots"),
    [("conf", None), ("full", None), ("full", ["tests/"])],
    ids=["confidential", "full-without-roots", "full-with-roots"],
)
def test_receiver_refuses_out_of_scope_added_test_paths(tmp_path, path, mode, roots):
    paths = ("tests/test_new.py", path)
    w = _world(tmp_path, paths)
    roots_arg = {"test_roots": roots} if roots is not None else {}
    pol = policy(w["state"], w["tmp"], w["tests"], **roots_arg)
    result = check(w, repo=mode, policy=pol)
    assert not result.ok and result.code == "TEST_SOURCE_MISMATCH", result


@pytest.mark.parametrize("path", ["tests/conftest.py", "tests/sub/conftest.py"])
def test_conftest_paths_remain_in_scope_for_added_bindings(path):
    assert not out_of_scope(path, "A")
    assert roots_problem(None, {"test_paths": [path]}, False, Path(".")) is None
