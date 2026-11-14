"""Route submit mirror branch checks (#1244)."""

import json

import pytest
from conftest import git

import cairnwork.intake as intake_module
from cairnwork.intake import Intake


@pytest.fixture
def route_submit(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(intake_module, "_spawn", lambda *_args, **_kwargs: None)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")
    mirror = tmp_path / "mirror"
    git(tmp_path, "clone", "-q", "--single-branch", "--branch", "main", str(repo), str(mirror))
    state = tmp_path / "state"
    intake = Intake(state)
    intake.set_accepting(True)
    (state / "config.json").write_text(json.dumps({"mirror": str(mirror)}))
    payload = {
        "idempotency_key": "route-branch-check",
        "card_hash": "route-branch-check-card",
        "title": "fix",
        "instructions": "fix it",
        "repo": str(repo),
        "base_commit": git(repo, "rev-parse", "HEAD").strip(),
        "allowed_files": ["src/a.py"],
        "verifiers": [["pytest", ["python3", "-m", "pytest", "tests/test_a.py"]]],
    }
    return intake, mirror, payload


@pytest.mark.parametrize(
    "mirror_category",
    [
        "wildcard fetch refspec",
        "no fetch refspec",
        "extra local branch",
        "extra origin branch",
        "non-origin remote branch",
        "second remote branch",
    ],
)
def test_route_submit_refuses_multi_branch_mirror(route_submit, mirror_category):
    _intake, mirror, payload = route_submit
    if mirror_category == "wildcard fetch refspec":
        git(mirror, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    elif mirror_category == "no fetch refspec":
        git(mirror, "config", "--unset-all", "remote.origin.fetch")
    elif mirror_category == "extra local branch":
        head = git(mirror, "rev-parse", "HEAD").strip()
        git(mirror, "update-ref", "refs/heads/hidden", head)
    elif mirror_category == "extra origin branch":
        head = git(mirror, "rev-parse", "HEAD").strip()
        git(mirror, "update-ref", "refs/remotes/origin/hidden", head)
    elif mirror_category == "non-origin remote branch":
        head = git(mirror, "rev-parse", "HEAD").strip()
        git(mirror, "update-ref", "refs/remotes/upstream/hidden", head)
    else:
        head = git(mirror, "rev-parse", "HEAD").strip()
        git(mirror, "remote", "add", "upstream", str(mirror))
        git(mirror, "config", "remote.upstream.fetch", "+refs/heads/*:refs/remotes/upstream/*")
        git(mirror, "update-ref", "refs/remotes/upstream/main", head)

    with pytest.raises(RuntimeError, match="LINEAGE_MIRROR_NOT_SINGLE_BRANCH"):
        _intake.submit(payload)


@pytest.mark.parametrize("mirror_category", ["single-branch fetch refspec and refs"])
def test_route_submit_accepts_single_branch_mirror(route_submit, mirror_category):
    intake, _mirror, payload = route_submit
    assert mirror_category
    out = intake.submit(payload)
    assert out["status"] == "BOUND" and not out["duplicate"]
