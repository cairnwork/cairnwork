"""#1501: already escaped exception reasons stay code-prefixed and single escaped."""

import re
import unicodedata
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from cairnwork import p2p, pipeline, verify_cli
from cairnwork.deferred_overlay import TestCommitRejected as CommitRejected
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.pipeline import TaskResult
from cairnwork.textclean import shown
from cairnwork.verify_context import VerifyContext
from cairnwork.workspace import CandidateRejected


def _assert_reason(reason, prefix):
    assert reason.startswith(prefix), reason
    assert not any(unicodedata.category(ch)[0] == "C" or ch in "\u2028\u2029" for ch in reason), (
        reason
    )
    assert re.search(r"\\{2,}'", reason) is None, reason


@pytest.mark.parametrize("pre_shown", [False, True])
def test_pipeline_preserves_code_and_single_escape_for_test_commit_error(
    monkeypatch, tmp_path, pre_shown
):
    hostile = "bad\n'quote"
    detail = shown(hostile) if pre_shown else hostile
    monkeypatch.setattr(pipeline, "tests_unused", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "check_roots_on_base",
        lambda *_a: (_ for _ in ()).throw(CommitRejected("TEST_COMMIT_UNREADABLE", detail)),
    )
    task = SimpleNamespace(
        before_attempt=None,
        card_hash="card",
        test_commit="a" * 40,
        repo=tmp_path,
        base_commit="b" * 40,
        test_roots=("tests",),
    )
    result = TaskResult("task", "attempt", "FAILED")
    ctx = VerifyContext(tmp_path, tmp_path)

    pipeline._attempt(task, (), "digest", None, None, result, None, ctx)

    _assert_reason(result.reason, "TEST_COMMIT_UNREADABLE:")


@pytest.mark.parametrize("pre_shown", [False, True])
def test_pipeline_candidate_refusal_keeps_drill_code_and_single_escape(
    monkeypatch, tmp_path, pre_shown
):
    hostile = "bad\n'quote"
    detail = shown(hostile) if pre_shown else hostile
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
        lambda *_a, **_k: (_ for _ in ()).throw(CandidateRejected("DIRTY_TREE", detail)),
    )
    task = SimpleNamespace(
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
    result = TaskResult("task", "attempt", "FAILED")
    steps = SimpleNamespace(reach=lambda *_a: None)
    ctx = VerifyContext(tmp_path, tmp_path)
    worker = SimpleNamespace(run=lambda *_a: None, run_as="")

    pipeline._attempt(task, (), "digest", None, worker, result, steps, ctx)

    _assert_reason(result.reason, "DIRTY_TREE:")


@pytest.mark.parametrize("pre_shown", [False, True])
def test_p2p_base_reason_preserves_code_and_single_escape(monkeypatch, tmp_path, pre_shown):
    hostile = "bad\n'quote"
    detail = shown(hostile) if pre_shown else hostile
    monkeypatch.setattr(
        p2p,
        "_base_tests",
        lambda *_a, **_k: (_ for _ in ()).throw(p2p.P2PRejected("P2P_BASE_UNREADABLE", detail)),
    )
    task = SimpleNamespace(p2p_mode="module-dir", test_commit="a" * 40)

    with pytest.raises(BaseUncomparable) as caught:
        p2p.base_env_check(task, None, VerifyContext(tmp_path, tmp_path))

    reason = caught.value.comparison.worker_reason(isolated=False)
    _assert_reason(reason, "BASE_ENV_FAILED:")
    assert "P2P_BASE_UNREADABLE:" in reason


@pytest.mark.parametrize("pre_shown", [False, True])
def test_verify_cli_import_refusal_preserves_code_and_single_escape(
    monkeypatch, tmp_path, pre_shown
):
    captured = {}
    hostile = "bad\n'quote"
    detail = shown(hostile) if pre_shown else hostile

    class Store:
        def import_bundle(self, *_args):
            raise CommitRejected("TEST_COMMIT_UNREADABLE", detail)

    class Intake:
        state = tmp_path

        def prepare(self):
            pass

        def test_store(self):
            return Store()

    monkeypatch.setattr(
        verify_cli,
        "_emit",
        lambda value, status: (captured.update(value=value, status=status), status)[1],
    )
    args = SimpleNamespace(repo_path=str(tmp_path), bundle=str(tmp_path / "bundle"))

    status = verify_cli._import_tests(Intake(), args)

    assert status == 2
    assert captured["status"] == 2
    assert captured["value"]["error"] == "TEST_COMMIT_UNREADABLE"
    _assert_reason(captured["value"]["detail"], "TEST_COMMIT_UNREADABLE:")


@pytest.mark.parametrize("pre_shown", [False, True])
def test_verify_cli_task_refusal_preserves_code_and_single_escape(monkeypatch, tmp_path, pre_shown):
    captured = {}
    hostile = "bad\n'quote"
    detail = shown(hostile) if pre_shown else hostile
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
    _assert_reason(captured["value"]["detail"], "BAD_IMPORT_PATH:")
