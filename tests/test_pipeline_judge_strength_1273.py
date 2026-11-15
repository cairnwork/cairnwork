"""Issue #1273: protect both final-verdict and receipt-binding checks."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import pipeline_judge
from cairnwork.verify_context import VerifyContext


def test_a_missing_verified_ledger_verdict_cannot_leave_the_result_verified(monkeypatch):
    task = SimpleNamespace(
        blackbox=None,
        repo=Path("r"),
        base_commit="b" * 40,
        import_paths=(),
        allowed_files=(),
        allow_deletions=False,
        require_protection="",
        test_commit=None,
    )
    result = SimpleNamespace(
        candidate_commit="c" * 40,
        task_id="task",
        attempt_id="attempt",
        logs=[],
        failure_evidence=[],
        verified=False,
        reason="",
    )
    run = SimpleNamespace(
        spec=SimpleNamespace(verifier_id="pytest-0"),
        log_path=Path("run.log"),
        observation=SimpleNamespace(passed=True),
        notes=(),
    )
    monkeypatch.setattr(pipeline_judge, "_refuse_candidate_sys_path", lambda *_: None)
    monkeypatch.setattr(pipeline_judge, "_candidate_runs", lambda *_: [run])
    monkeypatch.setattr(pipeline_judge.failure_evidence, "capture_for_task", lambda *_: [])
    monkeypatch.setattr(pipeline_judge, "_not_run_here", lambda *_: [])
    monkeypatch.setattr(pipeline_judge, "_not_run_refusal", lambda *_: "")
    monkeypatch.setattr(pipeline_judge, "raise_for_env_skips", lambda *_args, **_kwargs: None)

    core = SimpleNamespace(
        notarize=lambda *_args: SimpleNamespace(verified=True, status="VERIFIED", reason=""),
        verdict=lambda *_args, **_kwargs: SimpleNamespace(
            verified=False, status="FAILED_VERIFICATION", reason="LEDGER_NOT_VERIFIED"
        ),
    )
    pipeline_judge.judge_candidate(
        task,
        (),
        "digest",
        core,
        result,
        (),
        SimpleNamespace(),
        lambda *_: None,
    )

    assert result.verified is False


def test_a_binding_with_a_different_protection_level_stops_the_run(monkeypatch):
    from cairnwork import protection

    monkeypatch.setattr(pipeline_judge, "run_verifiers", lambda *a, **k: [])
    monkeypatch.setattr(pipeline_judge, "refuse_new_sys_path_mutation", lambda *a, **k: None)
    task = SimpleNamespace(
        blackbox=None,
        repo=Path("r"),
        base_commit="b" * 40,
        import_paths=(),
        allowed_files=(),
        allow_deletions=False,
        require_protection="",
        test_commit=None,
    )
    result = SimpleNamespace(candidate_commit="j", task_id="t", attempt_id="a", logs=[])

    with pytest.raises(RuntimeError, match="protection"):
        pipeline_judge.judge_candidate(
            task,
            (),
            "digest",
            None,
            result,
            (),
            VerifyContext.of(task, Path("x"), "a"),
            lambda *_: None,
            binding={"head": "h", "protection": protection.WEAKEST},
        )
