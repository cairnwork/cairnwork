"""Fast owner-side tests for the opt-in protected acceptance experiment."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cairnwork.blackbox import parse
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.verify_context import VerifyContext
from experimental.protected_accept import judge as judge_module
from experimental.protected_accept.once import OnceStore
from experimental.protected_accept.policy import declaration_problems
from experimental.protected_accept.run import run_protected

SALT = "ab" * 32
COMMIT = "c" * 40
CHECKOUT = "/tmp/checkout"


def declaration(values: list[dict]):
    cases = [
        {"id": f"c{index}", "args": [], "kwargs": {}, **value} for index, value in enumerate(values)
    ]
    raw = {
        "salt": SALT,
        "checks": [{"id": "add", "module": "sample", "function": "f", "cases": cases}],
    }
    return parse(json.dumps(raw).encode())


def answers(values: list, *, file: str = f"{CHECKOUT}/sample.py") -> bytes:
    cases = {}
    for index, value in enumerate(values):
        cases[f"c{index}"] = value
    return json.dumps({"checks": {"add": {"file": file, "cases": cases}}}).encode()


def run(report: bytes | None, **changes) -> BlackboxRun:
    fields: dict[str, Any] = dict(
        report=report,
        returncode=0,
        timed_out=False,
        survivors=0,
        mutated=(),
        forged=(),
        log_path=Path("/tmp/blackbox.log"),
        checkout=CHECKOUT,
        profile_sha256="profile",
        account=True,
    )
    return BlackboxRun(**(fields | changes))


def test_policy_entropy_and_case_count() -> None:
    for item in ({"expected": True}, {"expected": 0}, {"expected": ""}, {"expected": []}):
        assert declaration_problems(declaration([item])) == ("LOW_ENTROPY:add",)
    assert declaration_problems(declaration([{"expected": 5}])) == ("TOO_FEW_CASES:add",)
    assert declaration_problems(declaration([{"expected": 5}, {"expected": 5}])) == (
        "TOO_FEW_CASES:add",
    )
    assert declaration_problems(declaration([{"expected": 5}, {"expected": 9}])) == ()
    raised = {"raises": {"type": "ValueError", "message": "bad"}}
    assert declaration_problems(declaration([raised, {"expected": 9}])) == ()


def test_judge_outcomes_and_worker_view() -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])
    cases = [{"ok": 5}, {"ok": 9}]
    results = [
        judge_module.judge(good, COMMIT, run(answers(cases))),
        judge_module.judge(good, COMMIT, run(answers([{"ok": 6}, {"ok": 9}]))),
        judge_module.judge(declaration([{"expected": True}]), COMMIT, run(answers([{"ok": True}]))),
        judge_module.judge(good, COMMIT, run(answers(cases), account=False)),
        judge_module.judge(good, COMMIT, run(answers(cases), profile_sha256=None)),
        judge_module.judge(good, COMMIT, run(answers(cases), timed_out=True)),
        judge_module.judge(good, COMMIT, run(answers(cases), returncode=-9)),
        judge_module.judge(good, COMMIT, run(answers(cases), returncode=None)),
        judge_module.judge(good, COMMIT, run(b"not json")),
        judge_module.judge(good, COMMIT, run(answers(cases, file="/outside/sample.py"))),
        judge_module.judge(good, COMMIT, run(answers([{"ok": 5}]))),
    ]
    assert [(item.status, item.code) for item in results] == [
        ("PROTECTED_PASS", "OK"),
        ("MISMATCH", "MISMATCH:add"),
        ("REFUSED", "LOW_ENTROPY:add"),
        ("REFUSED", "NEEDS_VERIFIER_ACCOUNT"),
        ("INCOMPLETE", "NO_SANDBOX"),
        ("INCOMPLETE", "TIMEOUT"),
        ("INCOMPLETE", "KILLED"),
        ("INCOMPLETE", "NO_EXIT"),
        ("INCOMPLETE", "BAD_OUTPUT"),
        ("INCOMPLETE", "NOT_FROM_CHECKOUT"),
        ("INCOMPLETE", "BAD_OUTPUT"),
    ]
    assert all(item.status != "VERIFIED" for item in results)
    assert results[0].worker_view() == "PROTECTED_PASS"
    assert "matched" not in results[0].worker_view()


def test_once_store_is_exclusive_and_private(tmp_path: Path) -> None:
    store = OnceStore(tmp_path / "once")
    commitment = "a" * 64
    assert store.consume(commitment, COMMIT)
    assert not store.consume(commitment, "d" * 40)
    assert store.used(commitment)
    assert stat_mode(store.root / f"{commitment}.used") == 0o600
    with pytest.raises(ValueError):
        store.consume("../bad", COMMIT)


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def context(account: Any) -> VerifyContext:
    return VerifyContext(Path("/repo"), Path("/runs"), account=account)


def test_run_protected_lock_order_and_failures(tmp_path: Path, monkeypatch) -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])
    low = declaration([{"expected": True}])
    store = OnceStore(tmp_path / "run-once")
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("fail")

    monkeypatch.setattr("experimental.protected_accept.run.run_blackbox", fake_run)
    accountless = run_protected(good, context(None), COMMIT, "r1", store)
    assert (accountless.status, accountless.code) == ("REFUSED", "NEEDS_VERIFIER_ACCOUNT")
    digest = good.commitment().removeprefix("sha256:")
    assert not calls and not store.used(digest)
    low_result = run_protected(low, context(object()), COMMIT, "r2", store)
    assert (low_result.status, low_result.code) == ("REFUSED", "LOW_ENTROPY:add")
    assert not store.used(low.commitment().removeprefix("sha256:")) and not calls
    failed = run_protected(good, context(object()), COMMIT, "r3", store)
    assert (failed.status, failed.code) == ("INCOMPLETE", "RUN_ERROR:RuntimeError")
    assert store.used(digest)
    reused = run_protected(good, context(object()), COMMIT, "r4", store)
    assert (reused.status, reused.code) == ("REFUSED", "DECLARATION_REUSED")


def test_negative_control_owner_comparator_removed(monkeypatch) -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])
    monkeypatch.setattr("cairnwork.blackbox_judge.judge", lambda *_args: "match")
    result = judge_module.judge(good, COMMIT, run(answers([{"ok": 6}, {"ok": 9}])))
    assert (result.status, result.code) == ("PROTECTED_PASS", "OK")


def test_negative_control_entropy_guard_removed(monkeypatch) -> None:
    low = declaration([{"expected": True}])
    monkeypatch.setattr(judge_module, "declaration_problems", lambda _declaration: ())
    result = judge_module.judge(low, COMMIT, run(answers([{"ok": True}])))
    assert (result.status, result.code) == ("PROTECTED_PASS", "OK")


def test_negative_control_account_guard_removed(monkeypatch) -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])
    accountless = run(answers([{"ok": 5}, {"ok": 9}]), account=False)
    monkeypatch.setattr(judge_module, "_account_problem", lambda _run: None)
    result = judge_module.judge(good, COMMIT, accountless)
    # Without the guard the REFUSED code disappears; core's own check still keeps it non-passing.
    assert (result.status, result.code) == ("INCOMPLETE", "BLACKBOX_NEEDS_VERIFIER_ACCOUNT")


def test_class_check_with_only_degenerate_calls_is_low_entropy() -> None:
    def call(expected):
        return {"method": "m", "args": [], "kwargs": {}, "expected": expected}

    def case(cid, calls):
        return {"id": cid, "calls": calls}

    check: dict[str, Any] = {"id": "acc", "module": "sample", "class": "Acc"}
    check["cases"] = [case("k0", [call(0), call(1)]), case("k1", [call(1), call(0)])]
    raw = {"salt": SALT, "checks": [check]}
    assert declaration_problems(parse(json.dumps(raw).encode())) == ("LOW_ENTROPY:acc",)
    check["cases"][1] = case("k1", [call(7), call(11)])
    assert declaration_problems(parse(json.dumps(raw).encode())) == ("TOO_FEW_CASES:acc",)
    check["cases"][0] = case("k0", [call(0), call(12)])
    assert declaration_problems(parse(json.dumps(raw).encode())) == ()
