import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_deferred_tests import make_test_commit
from verify_helpers import git

from cairnwork import pipeline_judge, verifiers
from cairnwork.deferred_overlay import TestCommitRejected as _TestCommitRejected
from cairnwork.deferred_tests import BaseUncomparable, Comparison
from cairnwork.verifier_spec import VerifierSpec, parse_verifier
from cairnwork.verify_context import VerifyContext


def _probe(project, tmp_path, code, seed):
    source = project / "src" / "ordered.py"
    source.write_text(code)
    git(project, "add", "src/ordered.py")
    git(project, "commit", "-q", "-m", "ordered source")
    tests = "from ordered import joined\ndef test_joined():\n    assert joined() == 'alphabeta'\n"
    base, test_commit = make_test_commit(project, {"tests/test_ordered.py": tests})
    task = type(
        "Task",
        (),
        dict(
            repo=project,
            base_commit=base,
            test_commit=test_commit,
            test_roots=("tests",),
            test_store=None,
        ),
    )()
    spec = parse_verifier("v", ["python3", "-m", "pytest", "-q", "tests/test_ordered.py"])
    ctx = VerifyContext(project, tmp_path / "runs", attempt_id="attempt", hash_seed=str(seed))
    result = type("Result", (), dict(attempt_id="attempt", logs=[]))()
    return task, (spec,), ctx, result


def _fake_runs(
    monkeypatch, code, expected="alphabeta", outcomes_by_seed=None, survivors_by_seed=None
):
    target = "tests/test_ordered.py::test_joined"
    outputs = {}
    for seed in (1, 2, 3, 4):
        outputs[str(seed)] = subprocess.check_output(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            text=True,
        ).strip()

    def fake(ctx, _commit, specs, run_id, *, deny):
        outcome = "passed" if outputs.get(ctx.hash_seed, outputs["1"]) == expected else "failed"
        outcomes = (outcomes_by_seed or {}).get(ctx.hash_seed, {target: outcome})
        return tuple(
            SimpleNamespace(
                spec=spec,
                outcomes=outcomes,
                fail_kind={},
                returncode=0 if outcome == "passed" else 1,
                forged=(),
                data_refused=(),
                timed_out=False,
                survivors=(survivors_by_seed or {}).get(ctx.hash_seed, 0),
                mutated=(),
                report_too_big=False,
                log_too_big=False,
                collect_errors=(),
                recorded=True,
                teardown_errors=(),
                log_path=ctx.run_root / run_id / "logs.txt",
                deny_evidence="",
            )
            for spec in specs
        )

    monkeypatch.setattr(pipeline_judge, "run_verifiers", fake)
    monkeypatch.setattr(pipeline_judge, "as_failed", lambda run, *args: run)
    return outputs


def _sensitive_case(
    project,
    tmp_path,
    monkeypatch,
    *,
    expected="alphabeta",
    outcomes_by_seed=None,
    survivors_by_seed=None,
):
    _fake_runs(
        monkeypatch,
        "print(''.join({'alpha', 'beta'}))",
        expected=expected,
        outcomes_by_seed=outcomes_by_seed,
        survivors_by_seed=survivors_by_seed,
    )
    task, specs, ctx, result = _probe(
        project,
        tmp_path,
        "def joined():\n    return ''.join({'alpha', 'beta'})\n",
        1,
    )
    return task, specs, ctx, result


def test_hash_order_sensitive_base_is_uncomparable(project, tmp_path, monkeypatch):
    code = "print(''.join({'alpha', 'beta'}))"
    outputs = _fake_runs(monkeypatch, code)
    assert outputs["1"] != outputs["2"]
    task, specs, ctx, result = _probe(
        project,
        tmp_path,
        "def joined():\n    return ''.join({'alpha', 'beta'})\n",
        1,
    )
    with pytest.raises(BaseUncomparable) as caught:
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert caught.value.comparison.code == "BASE_SEED_SENSITIVE"
    assert "tests/test_ordered.py::test_joined" in caught.value.comparison.detail
    assert "seed 1" in caught.value.comparison.detail or "seed 2" in caught.value.comparison.detail


def test_order_independent_base_keeps_teeth(project, tmp_path, monkeypatch):
    code = "print(''.join(sorted({'alpha', 'beta'})))"
    outputs = _fake_runs(monkeypatch, code, expected="betaalpha")
    assert outputs["3"] == outputs["4"] != "betaalpha"
    task, specs, ctx, result = _probe(
        project,
        tmp_path,
        "def joined():\n    return ''.join(sorted({'alpha', 'beta'}))\n",
        3,
    )
    files = pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert files == ("tests/test_ordered.py",)


def test_second_run_uses_the_deterministic_distinct_seed(project, tmp_path, monkeypatch):
    outputs = _fake_runs(monkeypatch, "print(''.join({'alpha', 'beta'}))")
    assert outputs["1"] != outputs["2"]
    task, specs, ctx, result = _probe(
        project,
        tmp_path,
        "def joined():\n    return ''.join({'alpha', 'beta'})\n",
        1,
    )
    seen = []

    def record_seed(*args, **kwargs):
        seen.append(kwargs["hash_seed"])

    monkeypatch.setattr(verifiers, "_prepare_run", record_seed)
    real_fake = pipeline_judge.run_verifiers

    def invoke_prepare(ctx, *args, **kwargs):
        verifiers._prepare_run(
            tmp_path / "private", tmp_path / "pytest.ini", b"", hash_seed=ctx.hash_seed
        )
        return real_fake(ctx, *args, **kwargs)

    monkeypatch.setattr(pipeline_judge, "run_verifiers", invoke_prepare)
    with pytest.raises(BaseUncomparable):
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert seen == ["1", "2"]


def test_second_run_not_run_is_seed_sensitive(project, tmp_path, monkeypatch):
    target = "tests/test_ordered.py::test_joined"
    task, specs, ctx, result = _sensitive_case(
        project,
        tmp_path,
        monkeypatch,
        outcomes_by_seed={"1": {target: "failed"}, "2": {target: "failed"}},
    )
    comparisons = iter((Comparison("TEETH"), Comparison("NOT_RUN", "DEFERRED_TESTS_NOT_RUN")))
    monkeypatch.setattr(pipeline_judge, "base_comparison", lambda *args: next(comparisons))
    with pytest.raises(BaseUncomparable) as caught:
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert caught.value.comparison.code == "BASE_SEED_SENSITIVE"
    assert "NOT_RUN" in caught.value.comparison.detail


def test_second_run_base_problem_is_seed_sensitive(project, tmp_path, monkeypatch):
    task, specs, ctx, result = _sensitive_case(
        project,
        tmp_path,
        monkeypatch,
        expected="never matches",
        survivors_by_seed={"2": 1},
    )
    with pytest.raises(BaseUncomparable) as caught:
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert caught.value.comparison.code == "BASE_SEED_SENSITIVE"
    assert "UNCOMPARABLE BASE_SURVIVORS" in caught.value.comparison.detail


def test_parametrized_failures_are_compared_by_full_node_id(project, tmp_path, monkeypatch):
    alpha = "tests/test_ordered.py::test_joined[alpha]"
    beta = "tests/test_ordered.py::test_joined[beta]"
    task, specs, ctx, result = _sensitive_case(
        project,
        tmp_path,
        monkeypatch,
        outcomes_by_seed={"1": {alpha: "failed"}, "2": {beta: "failed"}},
    )
    with pytest.raises(BaseUncomparable) as caught:
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert caught.value.comparison.code == "BASE_SEED_SENSITIVE"
    assert repr(alpha) in caught.value.comparison.detail
    assert repr(beta) in caught.value.comparison.detail


def test_target_with_parameter_suffix_is_matched_by_prefix(monkeypatch):
    target = "tests/t.py::test_case[0]"
    run = SimpleNamespace(outcomes={target: "failed"})
    assert pipeline_judge._failed_targets([run], frozenset({target})) == {target}


@pytest.mark.parametrize("seed", ["", "not-a-number", "4294967296"])
def test_invalid_replay_seed_is_uncomparable(project, tmp_path, monkeypatch, seed):
    task, specs, ctx, result = _sensitive_case(project, tmp_path, monkeypatch)
    ctx = replace(ctx, hash_seed=seed)
    with pytest.raises(BaseUncomparable) as caught:
        pipeline_judge.tests_with_teeth(task, specs, result, ctx)
    assert caught.value.comparison == Comparison(
        "UNCOMPARABLE", "BASE_SEED_SENSITIVE", f"hash seed not a uint32: {seed!r}"
    )


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("NO_TEETH", "TESTS_DO_NOT_FAIL_ON_BASE"),
        ("NOT_RUN", "DEFERRED_TESTS_NOT_RUN"),
        ("UNCOMPARABLE", "BASE_TIMEOUT"),
    ],
)
def test_initial_refusal_does_not_start_second_run(monkeypatch, tmp_path, kind, code):
    monkeypatch.setattr(pipeline_judge, "check_roots_on_base", lambda *a: None)
    monkeypatch.setattr(pipeline_judge, "ensure_test_commit", lambda *a: None)
    monkeypatch.setattr(pipeline_judge, "test_paths", lambda *a: (("tests/test_x.py",), ()))
    monkeypatch.setattr(
        pipeline_judge, "new_tests", lambda *a: frozenset({"tests/test_x.py::test_x"})
    )
    monkeypatch.setattr(pipeline_judge, "check_conftests", lambda *a: None)
    monkeypatch.setattr(pipeline_judge, "untargeted", lambda *a: ())
    calls = []
    monkeypatch.setattr(pipeline_judge, "run_verifiers", lambda *a, **k: calls.append(a) or ())
    monkeypatch.setattr(pipeline_judge, "as_failed", lambda run, *a: run)
    monkeypatch.setattr(
        pipeline_judge,
        "base_comparison",
        lambda *a: Comparison(kind, code, "initial refusal"),
    )
    task = type(
        "Task",
        (),
        dict(
            repo=Path("/repo"),
            base_commit="base",
            test_commit="tests",
            test_roots=("tests",),
            test_store=None,
        ),
    )()
    spec = VerifierSpec("v", ("tests/test_x.py",))
    ctx = VerifyContext(Path("/repo"), tmp_path, attempt_id="attempt", hash_seed="1")
    result = type("Result", (), dict(attempt_id="attempt", logs=[]))()
    with pytest.raises((BaseUncomparable, _TestCommitRejected)):
        pipeline_judge.tests_with_teeth(task, (spec,), result, ctx)
    assert len(calls) == 1
