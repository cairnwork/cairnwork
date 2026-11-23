"""#1186: hidden-run reasons count failures without naming test-controlled identifiers."""

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import commit_change, git

from cairnwork.reasons import explain, skip_hint, why_not_passed
from cairnwork.verifiers import parse_verifier, run_verifiers
from cairnwork.verify_context import VerifyContext

BASE: dict[str, Any] = dict(
    ran_sandboxed=True,
    returncode=1,
    timed_out=False,
    mutated=(),
    survivors=0,
    log_too_big=False,
    tests_ran=True,
)


N = ("tests/CANARYfile.py::test_CANARYname",)
RC0: dict[str, Any] = dict(returncode=0, tests_ran=False)


@pytest.mark.parametrize(
    ("field", "value", "extra", "expected"),
    [
        ("mutated", ("tests/CANARYwritten.txt",), {}, "checkout changed (1)"),
        ("forged", ("tests/CANARY.pyc",), {}, "may not supply (1)"),
        ("data_refused", ("tests/CANARYdata.json",), {}, "declared test data executed (1)"),
        ("renamed", N, {}, "items renamed after collection (1)"),
        ("not_run", N, RC0, "skipped module (1)"),
    ],
)
def test_hidden_reason_channels_count_only(field, value, extra, expected):
    why = why_not_passed(**{**BASE, **extra, field: value}, hidden_tests=True)
    assert expected in why, why
    assert "CANARY" not in why


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


class TestOverlayNeverReachesTheAssertionPath:
    """format_failure: any overlay file means counts only, whichever node failed (#1186 R2)."""

    NODES = {"tests/test_visible.py::test_v": "failed", "tests/test_secret.py::test_s": "failed"}
    ASSERTIONS = {n: "E assert x == 'CANARY-assert'" for n in NODES}

    def _call(self, overlay, outcomes):
        from cairnwork.verifier_failure_summary import format_failure

        return format_failure("w", outcomes, None, overlay, self.ASSERTIONS)

    def test_overlay_with_failures_in_other_files_adds_no_assertion_text(self):
        only_visible = {"tests/test_visible.py::test_v": "failed"}
        assert self._call(("tests/test_secret.py",), only_visible) == "w"
        both = self._call(("tests/test_secret.py",), self.NODES)
        assert both == "w; deferred: 1 failed" and "CANARY" not in both

    def test_a_data_only_overlay_still_withholds_every_assertion(self):
        out = self._call(("data/fixture.json",), self.NODES)
        assert out == "w" and "CANARY" not in out

    def test_no_overlay_file_means_no_hidden_test_and_the_visible_summary_is_kept(self):
        assert "CANARY-assert" in self._call((), self.NODES)


# --- base comparison details (#1186, review round 2) ---
NODE = "tests/test_CANARYfile.py::test_CANARYname"
SHOWN = NODE.replace(":", "?")  # reasons._names masks the record delimiters


def _run(kind=None, hidden=True, outcome="failed"):
    return SimpleNamespace(
        spec=SimpleNamespace(verifier_id="v"), returncode=1, forged=False, data_refused=(),
        timed_out=False, survivors=0, mutated=(), report_too_big=False, log_too_big=False,
        collect_errors=(), recorded=True, teardown_errors=(), skipped_deps=(),
        outcomes={NODE: outcome}, fail_kind={NODE: kind} if kind else {}, hidden_tests=hidden,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "code", "visible"),
    [
        ("setup", "BASE_SETUP_ERROR", "fixture setup failed for 1: " + SHOWN),
        ("plugin", "PLUGIN_NOT_INSTALLED", "a pytest plugin fixture is missing for 1: " + SHOWN),
        ("env", "BASE_ENV_ERROR", "or a subprocess that could not run) for 1: " + SHOWN),
    ],
)
def test_base_teeth_detail_counts_when_hidden(kind, code, visible):
    from cairnwork.deferred_tests import base_comparison

    targets = frozenset({NODE})
    hidden = base_comparison([_run(kind)], targets)
    assert hidden.code == code and hidden.kind == "UNCOMPARABLE"
    assert "CANARY" not in hidden.detail and "1 test(s)" in hidden.detail
    shown = base_comparison([_run(kind, hidden=False)], targets)
    assert visible in shown.detail


def test_base_teeth_visible_text_is_exactly_main():
    from cairnwork.deferred_tests import base_comparison

    detail = base_comparison([_run("setup", hidden=False)], frozenset({NODE})).detail
    assert detail == (
        f"fixture setup failed for 1: {SHOWN}; "
        "import fix-introduced symbols inside the test function"
    )


def test_seed_sensitive_detail_counts_when_hidden():
    from cairnwork.seed_detail import seed_sensitive_detail

    teeth = SimpleNamespace(kind="TEETH", code="")
    hidden = seed_sensitive_detail([NODE], {NODE}, set(), "1", "2", teeth, True)
    assert "CANARY" not in hidden and "1 test(s)" in hidden
    shown = seed_sensitive_detail([NODE], {NODE}, set(), "1", "2", teeth)
    assert str([repr(NODE)]) in shown


SKIP = "tests/CANARY.py::test_CANARY"


@pytest.mark.parametrize(
    ("outcomes", "hidden_text", "visible_text"),
    [
        ({SKIP: "skipped"}, "(1 skipped)", "(1 skipped: tests/CANARY.py??test_CANARY)"),
        ({}, None, None),
        (
            {SKIP: "skipped", SKIP + "2": "xfailed", SKIP + "3": "passed"},
            "(1 skipped, 1 xfailed)",
            None,
        ),
    ],
)
def test_hidden_outcome_cause_counts_per_kind(outcomes, hidden_text, visible_text):
    args: dict[str, Any] = dict(BASE, **RC0, outcomes=outcomes)
    hidden = why_not_passed(**args, hidden_tests=True)
    visible = why_not_passed(**args)
    assert "CANARY" not in hidden and " failed)" not in hidden
    if hidden_text:
        assert f"pytest collected tests that did not pass {hidden_text}" in hidden, hidden
    else:
        assert "did not pass" not in hidden
        assert hidden == visible
    if visible_text:
        assert visible_text in visible, visible


@pytest.mark.parametrize("hidden", [True, False])
def test_seed_replay_refusal_counts_only_for_hidden_runs(monkeypatch, tmp_path, hidden):
    from cairnwork import pipeline_judge as pj

    first, second = _run(hidden=hidden), _run(hidden=hidden, outcome="passed")
    for run in (first, second):
        run.log_path = str(tmp_path / "x" / "log")
        run.deny_evidence = ""
    monkeypatch.setattr(pj, "run_verifiers", lambda *a, **k: [second])
    monkeypatch.setattr(pj, "as_failed", lambda run, *a: run)
    ctx = VerifyContext(tmp_path, tmp_path, hash_seed="1")
    result = SimpleNamespace(attempt_id="a", logs=[])
    with pytest.raises(pj.BaseUncomparable) as caught:
        pj._check_seed_replay(ctx, result, [first], frozenset({NODE}), "b", "t", None)
    detail = caught.value.comparison.detail
    assert ("CANARY" not in detail) is hidden
    assert ("1 test(s)" in detail) is hidden
