"""#1186: hidden-run reasons count failures without naming test-controlled identifiers."""

import sys
from types import SimpleNamespace

import pytest
from conftest import commit_change, git

from cairnwork.reasons import explain, skip_hint, why_not_passed
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

BASE = dict(
    ran_sandboxed=True,
    returncode=1,
    timed_out=False,
    mutated=(),
    survivors=0,
    log_too_big=False,
    tests_ran=True,
)


@pytest.mark.parametrize(
    ("field", "value", "canaries"),
    [
        ("mutated", ("tests/CANARYwritten.txt",), ("CANARYwritten",)),
        ("forged", ("tests/CANARY.pyc",), ("CANARY",)),
        ("data_refused", ("tests/CANARYdata.json",), ("CANARYdata",)),
        ("renamed", ("tests/CANARYfile.py::test_CANARYname",), ("CANARYfile", "CANARYname")),
        ("not_run", ("tests/CANARYfile.py::test_CANARYname",), ("CANARYfile", "CANARYname")),
    ],
)
def test_hidden_reason_channels_count_only(field, value, canaries):
    args = dict(BASE, hidden_tests=True, **{field: value})
    why = why_not_passed(**args)
    assert "1" in why
    assert all(secret not in why for secret in canaries)


def test_hidden_setup_kind_denial_and_outcome_channels_count_only():
    why = why_not_passed(
        **BASE,
        hidden_tests=True,
        fail_kind={"tests/CANARY.py::test_CANARY": "setup"},
        outcomes={"tests/CANARY.py::test_CANARY": "failed"},
    )
    assert "setup error (1)" in why
    assert all(secret not in why for secret in ("CANARY", "private"))
    ordinary = why_not_passed(
        **dict(BASE, returncode=0, tests_ran=False),
        hidden_tests=True,
        outcomes={"tests/CANARY.py::test_CANARY": "failed"},
    )
    assert "1 failed" in ordinary and "CANARY" not in ordinary


def test_visible_reason_text_is_unchanged():
    assert why_not_passed(**dict(BASE, returncode=0, mutated=("tests/visible.txt",))) == (
        "checkout changed (1: tests/visible.txt)"
    )


def test_hidden_explain_hides_modules_and_skip_names():
    run = SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"),
        why="pytest exit code 1",
        observation=SimpleNamespace(passed=False),
        notes=(),
        hidden_tests=True,
        submodule_errors=(("tests/CANARY.py::test_CANARY", "CANARYmodule", "pkg"),),
        outcomes={"tests/CANARY.py::test_CANARY": "failed"},
        skips={},
    )
    text = explain([run])
    assert "collection could not import 1 module(s)" in text
    assert "CANARY" not in text


def test_hidden_skip_hint_counts_modules():
    run = SimpleNamespace(hidden_tests=True, skipped_deps=("CANARYmodule",))
    assert skip_hint(run) == "; skipped at collection, could not import 1 module(s)"


@pytest.mark.sandbox
@pytest.mark.parametrize(
    ("path", "source", "canaries"),
    [
        (
            "tests/test_mutator.py",
            "from pathlib import Path\ndef test_mutate():\n"
            "    Path('tests/CANARYwritten.txt').write_text('x')\n    assert False\n",
            ("CANARYwritten",),
        ),
        (
            "tests/test_CANARYfile.py",
            "import pytest\n@pytest.fixture\ndef setup_canary():\n"
            "    raise RuntimeError('setup failed')\n"
            "def test_CANARYname(setup_canary): pass\n",
            ("CANARYfile", "CANARYname"),
        ),
    ],
)
def test_real_hidden_verifier_reasons_count_only(repo, tmp_path, path, source, canaries):
    base = git(repo, "rev-parse", "HEAD")
    hidden = commit_change(repo, {path: source})
    spec = parse_verifier("pytest", ["python3", "-m", "pytest", "-q", path])
    ctx = VerifyContext(
        repo, tmp_path / "runs", python=sys.executable, base_commit=base, test_commit=hidden
    )
    run = run_verifiers(ctx, hidden, (spec,), "canary")[0]
    assert not run.observation.passed
    assert all(secret not in run.why for secret in canaries)
    assert "1" in run.why


def _base_run(**over):
    run = dict(
        returncode=1, forged=(), data_refused=(), timed_out=False, survivors=(), mutated=(),
        report_too_big=False, log_too_big=False, collect_errors={}, recorded=True,
        teardown_errors=(), outcomes={"tests/test_ok.py::test_a": "failed"}, skipped_deps=(),
        skip_origins={}, hidden_tests=True, spec=SimpleNamespace(verifier_id="pytest"),
    )  # fmt: skip
    return SimpleNamespace(**{**run, **over})


def test_hidden_base_comparison_collect_errors_are_a_count():
    from cairnwork.deferred_tests import base_comparison

    run = _base_run(collect_errors={"tests/test_CANARYfile.py": "boom"})
    out = base_comparison([run], frozenset({"tests/test_CANARYfile.py"})).detail
    assert "CANARYfile" not in out and "collection errors 1" in out
    shown = base_comparison([_base_run(collect_errors=run.collect_errors, hidden_tests=False)],
                            frozenset({"tests/test_CANARYfile.py"})).detail  # fmt: skip
    assert "CANARYfile" in shown


def test_hidden_base_comparison_not_run_is_a_count():
    from cairnwork.deferred_tests import base_comparison

    targets = frozenset({"tests/test_CANARYname.py::test_x"})
    assert "CANARYname" not in base_comparison([_base_run()], targets).detail
    assert "CANARYname" in base_comparison([_base_run(hidden_tests=False)], targets).detail


def test_denied_path_is_named_for_visible_runs_and_withheld_for_hidden_ones():
    args = dict(BASE, denied_path="/private/CANARY-path")
    visible = why_not_passed(**args)
    assert "/private/CANARY-path" in visible and "EPERM" in visible
    hidden = why_not_passed(**args, hidden_tests=True)
    assert "CANARY" not in hidden and "1 denied path (withheld)" in hidden
