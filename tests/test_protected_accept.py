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
    assert stat_mode(store.root) == 0o700
    commitment = "a" * 64
    assert store.consume(commitment, COMMIT)
    assert not store.consume(commitment, "d" * 40)
    assert store.used(commitment)
    assert stat_mode(store.root / f"{commitment}.used") == 0o600
    with pytest.raises(ValueError):
        store.consume("../bad", COMMIT)


def test_once_store_rejects_symlink_root_and_used_link(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        OnceStore(root_link)
    store = OnceStore(tmp_path / "once-links")
    dangling = store.root / f"{'b' * 64}.used"
    dangling.symlink_to(tmp_path / "missing")
    assert store.used("b" * 64)
    assert not store.consume("b" * 64, COMMIT)


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
    bad_commit = run_protected(good, context(object()), "bad", "r0", store)
    assert (bad_commit.status, bad_commit.code) == ("REFUSED", "BAD_COMMIT")
    assert not store.used(good.commitment().removeprefix("sha256:"))
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


def test_run_protected_passes_zero_timeout(tmp_path: Path, monkeypatch) -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])
    seen = []

    def fake_run(*_args, **kwargs):
        seen.append(kwargs)
        return run(answers([{"ok": 5}, {"ok": 9}]))

    monkeypatch.setattr("experimental.protected_accept.run.run_blackbox", fake_run)
    result = run_protected(
        good, context(object()), COMMIT, "r1", OnceStore(tmp_path / "once"), timeout=0
    )
    assert (result.status, result.code) == ("PROTECTED_PASS", "OK")
    assert seen == [{"timeout": 0}]


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


@pytest.mark.parametrize(
    "values",
    [
        pytest.param(
            (
                "function",
                {"expected": 5.0, "tolerance": {"abs_tol": 3}},
                {"expected": 7.0, "tolerance": {"abs_tol": 3}},
            ),
            id="abs-overlap",
        ),
        pytest.param(
            (
                "function",
                {"expected": 5.0, "tolerance": {"rel_tol": 0.9}},
                {"expected": 7.0, "tolerance": {"rel_tol": 0.9}},
            ),
            id="rel-overlap",
        ),
        pytest.param(("function", {"expected": [True]}, {"expected": [False]}), id="bool-leaf"),
        pytest.param(("function", {"expected": [0]}, {"expected": [1]}), id="int-leaf"),
        pytest.param(("function", {"expected": [None]}, {"expected": [[]]}), id="null-empty"),
        pytest.param(
            ("function", {"expected": {"a": True}}, {"expected": {"a": False}}), id="dict-value"
        ),
        pytest.param(
            (
                "function",
                {"expected": 1.0, "tolerance": {"abs_tol": 0}},
                {"expected": -1.0, "tolerance": {"abs_tol": 0}},
            ),
            id="float-sign",
        ),
        pytest.param(("class", [12, True], [12, False]), id="class-bool-call"),
        pytest.param(("class", [12, 0], [12, 1]), id="class-int-call"),
        pytest.param(
            (
                "class",
                [12, {"expected": 5.0, "tolerance": {"abs_tol": 3}}],
                [12, {"expected": 7.0, "tolerance": {"abs_tol": 3}}],
            ),
            id="class-tolerance-overlap",
        ),
        pytest.param(
            ("function", *({"expected": [x], "tolerance": {"abs_tol": 0.1}} for x in (2.0, 2.1))),
            id="nested-tolerance-overlap",
        ),
        pytest.param(
            (
                "function",
                *({"expected": {"a": [x, 9]}, "tolerance": {"abs_tol": 0.5}} for x in (2.0, 2.5)),
            ),
            id="deep-tolerance-overlap",
        ),
    ],
)
def test_constant_or_guessable_report_is_refused(values) -> None:
    """A report guessed from constants or degenerate leaves must not pass precheck."""
    kind, first, second = values
    if kind == "function":
        decl = declaration([first, second])
    else:

        def call(value):
            outcome = value if isinstance(value, dict) else {"expected": value}
            return {"method": "m", "args": [], "kwargs": {}, **outcome}

        check = {
            "id": "add",
            "module": "sample",
            "class": "Thing",
            "cases": [
                {"id": "k0", "calls": [call(v) for v in first]},
                {"id": "k1", "calls": [call(v) for v in second]},
            ],
        }
        decl = parse(json.dumps({"salt": SALT, "checks": [check]}).encode())
    assert declaration_problems(decl)


@pytest.mark.parametrize(
    "values",
    [
        pytest.param([{"expected": 5}, {"expected": 9}], id="integers"),
        pytest.param([{"expected": 2}, {"expected": 3}], id="nearby-integers"),
        pytest.param([{"expected": 2}, {"expected": -2}], id="signed-integers"),
        pytest.param(
            [{"raises": {"type": "ValueError", "message": "bad"}}, {"expected": 9}],
            id="raise-and-value",
        ),
        pytest.param(
            [
                {"raises": {"type": "ValueError", "message": "bad"}},
                {"raises": {"type": "ValueError", "message": "worse"}},
            ],
            id="raises",
        ),
        pytest.param(
            [
                {"expected": 0.5, "tolerance": {"abs_tol": 1e-9}},
                {"expected": 0.25, "tolerance": {"abs_tol": 1e-9}},
            ],
            id="small-floats",
        ),
        pytest.param(
            [
                {"expected": 5.0, "tolerance": {"abs_tol": 0.5}},
                {"expected": 7.0, "tolerance": {"abs_tol": 0.5}},
            ],
            id="separate-windows",
        ),
        pytest.param([{"expected": [3, 4]}, {"expected": [5]}], id="lists"),
        pytest.param({"class_calls": [[0, 12], [7, 11]]}, id="class-calls"),
        pytest.param(
            [{"expected": [x], "tolerance": {"abs_tol": 0.1}} for x in (2.0, 2.3)],
            id="nested-separate-windows",
        ),
    ],
)
def test_honest_declarations_pass_precheck(values) -> None:
    if isinstance(values, dict):

        def call(value):
            outcome = value if isinstance(value, dict) else {"expected": value}
            return {"method": "m", "args": [], "kwargs": {}, **outcome}

        check = {
            "id": "add",
            "module": "sample",
            "class": "Thing",
            "cases": [
                {"id": f"k{index}", "calls": [call(value) for value in calls]}
                for index, calls in enumerate(values["class_calls"])
            ],
        }
        decl = parse(json.dumps({"salt": SALT, "checks": [check]}).encode())
    else:
        decl = declaration(values)
    assert declaration_problems(decl) == ()


def test_one_failing_check_of_several_is_mismatch() -> None:
    raw = {
        "salt": SALT,
        "checks": [
            {
                "id": name,
                "module": "sample",
                "function": name,
                "cases": [
                    {"id": f"c{i}", "args": [], "expected": val} for i, val in enumerate((5, 9))
                ],
            }
            for name in ("x", "y")
        ],
    }
    decl = parse(json.dumps(raw).encode())
    report = json.dumps(
        {
            "checks": {
                "x": {"file": f"{CHECKOUT}/sample.py", "cases": {"c0": {"ok": 5}, "c1": {"ok": 9}}},
                "y": {"file": f"{CHECKOUT}/sample.py", "cases": {"c0": {"ok": 6}, "c1": {"ok": 9}}},
            }
        }
    ).encode()
    result = judge_module.judge(decl, COMMIT, run(report))
    assert (result.status, result.code) == ("MISMATCH", "MISMATCH:y")


def test_judge_error_is_incomplete(monkeypatch) -> None:
    good = declaration([{"expected": 5}, {"expected": 9}])

    def fail(*_args):
        raise RuntimeError("oracle error")

    monkeypatch.setattr(judge_module, "judge_run", fail)
    result = judge_module.judge(good, COMMIT, run(answers([{"ok": 5}, {"ok": 9}])))
    assert (result.status, result.code) == ("INCOMPLETE", "JUDGE_ERROR")
