"""#1501: exception text is escaped at generic pipeline and CLI output sinks."""

import unicodedata
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from cairnwork import p2p, pipeline, verify_cli
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.pipeline import TaskResult
from cairnwork.textclean import shown
from cairnwork.verify_context import VerifyContext
from cairnwork.workspace import CandidateRejected

HOSTILE = "bad\n'quote"


def _assert_once(reason: str, expected: str) -> None:
    assert reason == expected
    assert not any(unicodedata.category(ch)[0] == "C" or ch in "\u2028\u2029" for ch in reason)
    assert "\\\\x0a" not in reason


def _task(tmp_path):
    return SimpleNamespace(
        before_attempt=None,
        card_hash="card",
        test_commit="",
        repo=tmp_path,
        base_commit="b" * 40,
        test_roots=("tests",),
        p2p_mode="off",
        allowed_files=("src/app.py",),
        allow_deletions=False,
    )


def _run_candidate_refusal(monkeypatch, tmp_path, detail, code="DIRTY_TREE"):
    monkeypatch.setattr(pipeline, "tests_unused", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "check_roots_on_base", lambda *_a: None)
    monkeypatch.setattr(pipeline, "precheck", lambda *_a: None)
    monkeypatch.setattr(pipeline, "base_stage", lambda *_a: nullcontext())
    monkeypatch.setattr(
        pipeline, "create_attempt_clone", lambda *_a: SimpleNamespace(path=tmp_path)
    )
    monkeypatch.setattr(pipeline, "unless_interrupted", lambda *_a: nullcontext())
    monkeypatch.setattr(
        pipeline,
        "snapshot_candidate",
        lambda *_a, **_k: (_ for _ in ()).throw(CandidateRejected(code, detail)),
    )
    result = TaskResult("task", "attempt", "FAILED")
    worker = SimpleNamespace(run=lambda *_a: None, run_as="")
    pipeline._attempt(
        _task(tmp_path),
        (),
        "digest",
        None,
        worker,
        result,
        SimpleNamespace(reach=lambda *_a: None),
        VerifyContext(tmp_path, tmp_path),
    )
    return result.reason


def test_pipeline_keeps_workspace_multi_item_detail_escaped_once(monkeypatch, tmp_path):
    detail = ", ".join(shown(path) for path in ("bad\nname.py", "other.py"))
    reason = _run_candidate_refusal(monkeypatch, tmp_path, detail)
    _assert_once(reason, f"DIRTY_TREE: {detail}")


def test_workspace_and_pipeline_plain_refusal_text_matches_main(monkeypatch, tmp_path):
    refusal = CandidateRejected("OUT_OF_SCOPE", "src/private.py")
    assert str(refusal) == "OUT_OF_SCOPE: src/private.py"
    reason = _run_candidate_refusal(monkeypatch, tmp_path, "src/private.py", "OUT_OF_SCOPE")
    assert reason == "OUT_OF_SCOPE: src/private.py"


def test_pipeline_worker_failure_escapes_raw_exception_once(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "tests_unused", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "check_roots_on_base", lambda *_a: None)
    monkeypatch.setattr(pipeline, "precheck", lambda *_a: None)
    monkeypatch.setattr(pipeline, "base_stage", lambda *_a: nullcontext())
    monkeypatch.setattr(
        pipeline, "create_attempt_clone", lambda *_a: SimpleNamespace(path=tmp_path)
    )
    monkeypatch.setattr(pipeline, "unless_interrupted", lambda *_a: nullcontext())

    def fail(*_args):
        raise RuntimeError(HOSTILE)

    result = TaskResult("task", "attempt", "FAILED")
    pipeline._attempt(
        _task(tmp_path),
        (),
        "digest",
        None,
        SimpleNamespace(run=fail, run_as=""),
        result,
        SimpleNamespace(reach=lambda *_a: None),
        VerifyContext(tmp_path, tmp_path),
    )
    _assert_once(result.reason, f"WORKER_FAILED: RuntimeError: {shown(HOSTILE)}")


def test_p2p_base_refusal_keeps_code_prefix_without_sandbox(monkeypatch, tmp_path):
    detail = shown(HOSTILE)
    monkeypatch.setattr(
        p2p,
        "_base_tests",
        lambda *_a, **_k: (_ for _ in ()).throw(p2p.P2PRejected("P2P_BASE_UNREADABLE", detail)),
    )
    task = SimpleNamespace(p2p_mode="module-dir", test_commit="a" * 40)

    with pytest.raises(BaseUncomparable) as caught:
        p2p.base_env_check(task, None, VerifyContext(tmp_path, tmp_path))

    reason = caught.value.comparison.worker_reason(isolated=False)
    _assert_once(reason, f"BASE_ENV_FAILED: unchanged base: P2P_BASE_UNREADABLE: {detail}")


def test_verify_cli_task_refusal_preserves_prefix_without_changing_main_text(monkeypatch, tmp_path):
    detail = shown(HOSTILE)
    captured = {}
    monkeypatch.setattr(verify_cli, "_intake", lambda _args: object())
    monkeypatch.setattr(
        verify_cli,
        "_run",
        lambda *_args: (_ for _ in ()).throw(pipeline.TaskRejected("BAD_IMPORT_PATH", detail)),
    )
    monkeypatch.setattr(
        verify_cli,
        "_emit",
        lambda value, status: (captured.update(value=value, status=status), status)[1],
    )
    argv = [
        "--state",
        str(tmp_path),
        "run",
        "--repo-path",
        str(tmp_path),
        "--base",
        "a" * 40,
        "--candidate",
        "b" * 40,
        "--allowed-file",
        "src/app.py",
        "--verifier",
        "pytest",
    ]

    status = verify_cli.main(argv)

    assert status == 2
    assert captured["value"]["reason"] == "BAD_IMPORT_PATH"
    assert captured["value"]["detail"] == f"BAD_IMPORT_PATH: {detail}"
