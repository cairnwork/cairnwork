"""Programs named in a worker's .git/config never run when Cairnwork collects its commit (#727)."""

import pytest
from conftest import git

from cairnwork.workspace import Attempt, _guarded_refs, collect_candidate, create_attempt_clone

WORKER = ("-c", "user.email=w@example.invalid", "-c", "user.name=w")


@pytest.mark.parametrize(
    "key",
    [
        "core.alternateRefsCommand",
        "uploadpack.packObjectsHook",
        "core.fsmonitor",
        "core.sshCommand",
    ],
)
def test_worker_config_program_never_runs_on_collect(repo, tmp_path, key):
    base = git(repo, "rev-parse", "HEAD")
    made = create_attempt_clone(repo, base, tmp_path / "attempts", "a1")
    clone = made.path
    (clone / "src").mkdir(exist_ok=True)
    (clone / "src" / "a.py").write_text("x = 3\n")
    git(clone, "add", "-A")
    git(clone, *WORKER, "commit", "-q", "-m", "candidate")
    marker = tmp_path / "worker-code-ran"
    git(clone, "config", key, f"touch {marker}")
    attempt = Attempt("a1", base, clone, _guarded_refs(repo, "a1"))
    collect_candidate(repo, attempt, ("src/a.py",))
    assert not marker.exists()
