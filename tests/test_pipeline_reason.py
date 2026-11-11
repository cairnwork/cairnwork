"""The verdict->reason mapping (Issue #74 item 3 / #109): the two reason-text mutants that survived
mutation review — `shown = recorded or verdict` and the CORE_NOT_VERIFIED fallback — are killed here
by testing the extracted pure function directly."""

from types import SimpleNamespace
from typing import Any

from cairnwork.reasons import verdict_reason, why_not_passed


def test_not_run_refusal_precedes_environment_skip(monkeypatch, tmp_path):
    from cairnwork import pipeline_judge

    task = SimpleNamespace(
        blackbox=None,
        repo=tmp_path,
        base_commit="base",
        allowed_files=(),
        test_commit=None,
    )
    result = SimpleNamespace(
        candidate_commit="candidate",
        task_id="task",
        attempt_id="attempt",
        logs=[],
        failure_evidence=[],
        reason="",
        verified=False,
    )
    run = SimpleNamespace(log_path=tmp_path / "run.log", observation=SimpleNamespace(passed=True))
    monkeypatch.setattr(pipeline_judge, "_refuse_candidate_sys_path", lambda *_: None)
    monkeypatch.setattr(pipeline_judge, "_candidate_runs", lambda *_: [run])
    monkeypatch.setattr(pipeline_judge.failure_evidence, "capture_for_task", lambda *_: [])
    monkeypatch.setattr(pipeline_judge, "_not_run_refusal", lambda *_: "NAMED_TESTS_NOT_RUN")
    monkeypatch.setattr(
        pipeline_judge,
        "raise_for_env_skips",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called too early")),
    )
    pipeline_judge.judge_candidate(
        task, (), "digest", None, result, (), SimpleNamespace(), lambda *_: None
    )
    assert result.reason == "NAMED_TESTS_NOT_RUN"


def _v(status, reason=""):
    return SimpleNamespace(status=status, reason=reason)


def test_a_verified_attempt_reads_core_status():
    assert verdict_reason(True, _v("VERIFIED"), _v("VERIFIED")) == "CORE_VERIFIED"


def test_the_readback_reason_is_shown_not_the_notarize_return():
    # Kills `shown = verdict`: the readback (recorded) reason must win over the notarize verdict's,
    # so an unverified readback never reads as the verdict's own (possibly empty / VERIFIED) reason.
    verdict, recorded = (
        _v("VERIFIED", reason="from-verdict"),
        _v("VERIFIED", "CORE_IDENTITY_MISMATCH"),
    )
    assert verdict_reason(False, verdict, recorded) == "CORE_IDENTITY_MISMATCH"


def test_without_a_readback_theverdict_reason_is_used():
    assert verdict_reason(False, _v("VERIFIED", reason="CORE_MANIFEST_HASH_MISMATCH"), None) == (
        "CORE_MANIFEST_HASH_MISMATCH"
    )


def test_verified_status_but_unverified_readback_with_no_reason_is_core_not_verified():
    # Kills the CORE_NOT_VERIFIED fallback mutant: Core said VERIFIED but the readback did not
    # verify and set no reason, so it must read CORE_NOT_VERIFIED, not CORE_VERIFIED.
    assert verdict_reason(False, _v("VERIFIED"), _v("VERIFIED", "")) == "CORE_NOT_VERIFIED"


def test_a_non_verified_status_with_no_reason_reads_core_status():
    assert verdict_reason(False, _v("FAILED_VERIFICATION"), _v("FAILED_VERIFICATION")) == (
        "CORE_FAILED_VERIFICATION"
    )


def test_an_oversized_test_report_is_a_named_cause_only_when_flagged():
    base: dict[str, Any] = dict(
        ran_sandboxed=True, returncode=0, timed_out=False, mutated=(), survivors=0,
        log_too_big=False, tests_ran=False,
    )  # fmt: skip
    assert "test report over the size cap" not in why_not_passed(**base)
    assert "test report over the size cap" in why_not_passed(**base, report_too_big=True)


def test_all_skipped_has_a_specific_failure_reason_but_default_stays_compatible():
    base: dict[str, Any] = dict(
        ran_sandboxed=True, returncode=0, timed_out=False, mutated=(), survivors=0,
        log_too_big=False, tests_ran=False,
    )  # fmt: skip
    assert why_not_passed(**base) == "pytest passed but the guard saw no test run"
    assert why_not_passed(**base, all_skipped=True) == (
        "pytest passed but every collected test was skipped"
    )
