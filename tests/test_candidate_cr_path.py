import sys

import pytest
from conftest import git
from verify_helpers import FIX, base_of, candidate, verify

from cairnwork.workspace import (
    Attempt,
    CandidateRejected,
    _guarded_refs,
    collect_candidate,
    create_attempt_clone,
)

CR_PATHS = (
    pytest.param("src/mid\rname.py", id="cr-middle"),
    pytest.param("src/a.py\r", id="cr-ending"),
    pytest.param("src/pair\r\nname.py", id="cr-lf-pair"),
)


def _attempt(repo, tmp_path, attempt_id):
    base = git(repo, "rev-parse", "HEAD")
    made = create_attempt_clone(repo, base, tmp_path / "attempts", attempt_id)
    return Attempt(attempt_id, base, made.path, _guarded_refs(repo, attempt_id)), made.path


def _commit(clone):
    git(clone, "add", "-A")
    git(
        clone,
        "-c",
        "user.email=w@example.invalid",
        "-c",
        "user.name=w",
        "commit",
        "-q",
        "-m",
        "candidate",
    )


@pytest.mark.parametrize("cr_path", CR_PATHS)
def test_worker_rejects_cr_paths_and_accepts_the_allowed_change(repo, tmp_path, cr_path):
    attempt, clone = _attempt(repo, tmp_path, "with-cr")
    (clone / "src" / "a.py").write_text("x = 2\n")
    extra = clone / cr_path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("z = 1\n")
    _commit(clone)

    with pytest.raises(CandidateRejected) as info:
        collect_candidate(repo, attempt, ("src/a.py",), python=sys.executable)
    assert info.value.code == "OUT_OF_SCOPE"

    control_attempt, control_clone = _attempt(repo, tmp_path, "without-cr")
    (control_clone / "src" / "a.py").write_text("x = 2\n")
    _commit(control_clone)
    try:
        collect_candidate(repo, control_attempt, ("src/a.py",), python=sys.executable)
    except CandidateRejected as control:
        assert control.code != "OUT_OF_SCOPE"


@pytest.mark.parametrize("cr_path", CR_PATHS)
@pytest.mark.parametrize("include_fix", [True, False], ids=["with-fix", "cr-only"])
def test_verify_refuses_cr_paths(project, state, cr_path, include_fix):
    base = base_of(project)
    files = {cr_path: "value = 1\n"}
    if include_fix:
        files["src/calc.py"] = FIX
    cand = candidate(project, base, files)

    code, out = verify(state, project, base, cand)
    assert code == 2
    assert out["status"] == "REFUSED"
    assert out["verified"] is False
    assert out["core_status"] == ""
