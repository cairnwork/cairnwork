"""#537 on the cairnwork-verify path: a run ended by KeyboardInterrupt or SystemExit keeps its
marker and its clone, so the next run's ``cleanup`` still finds the attempt, sweeps its processes
and removes the clone. A normal return or an ordinary exception removes both at once."""

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change, git

import cairnwork.verify_candidate as verify_candidate

ENDS = [KeyboardInterrupt(), SystemExit(3), RuntimeError("ordinary")]


def _kept(exc: BaseException) -> bool:
    return not isinstance(exc, Exception)


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_the_run_marker_is_kept_only_on_an_interrupt(tmp_path, end):
    intake: Any = SimpleNamespace(state=tmp_path)
    with pytest.raises(type(end)), verify_candidate._marked(intake, "a1"):
        raise end
    assert (tmp_path / "verify-runs" / "a1").exists() is _kept(end)


def test_a_finished_run_removes_its_marker(tmp_path):
    intake: Any = SimpleNamespace(state=tmp_path)
    with verify_candidate._marked(intake, "a1"):
        pass
    assert not (tmp_path / "verify-runs" / "a1").exists()


@pytest.mark.parametrize("end", ENDS, ids=type)
def test_the_verify_clone_is_kept_only_on_an_interrupt(monkeypatch, repo, tmp_path, end):
    def stop(*args):
        raise end

    monkeypatch.setattr(verify_candidate, "check_candidate_commit", stop)
    base = git(repo, "rev-parse", "HEAD")
    head = commit_change(repo, {"src/a.py": "x = 2\n"})
    runs = tmp_path / "runs"
    runs.mkdir()
    task: Any = SimpleNamespace(
        repo=repo, base_commit=base, test_roots=("tests",), allowed_files=("src/a.py",)
    )
    result: Any = SimpleNamespace(attempt_id="a1", logs=[])
    ctx: Any = SimpleNamespace(run_root=runs)
    with pytest.raises(type(end)):
        verify_candidate._judge_in(None, task, (), head, None, result, ctx, source=repo)
    assert (runs / "a1").exists() is _kept(end)
