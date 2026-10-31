"""#117 fail-closed rules of the P2P plan and candidate run (p2p.py), with the probe and the
verifier runs stubbed so each rule is checked on its own (test_p2p.py runs them for real)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairnwork import deferred_tests, p2p
from cairnwork.core_request import VerifierObservation
from cairnwork.deferred_tests import BaseUncomparable
from cairnwork.gitcmd import GitCommandError
from cairnwork.p2p import P2PPlan, P2PRejected, plan_p2p, run_plan
from cairnwork.p2p_nodes import changed_tests
from cairnwork.verifiers import VerifierRun, VerifierSpec
from cairnwork.verify_context import VerifyContext

TASK = SimpleNamespace(
    repo="/repo",
    base_commit="b" * 40,
    test_roots=("tests",),
    import_paths=(),
    allowed_files=("src/pkg/a/b.py",),
)
RESULT = SimpleNamespace(attempt_id="att", logs=[])  # #331: an unusable probe adds a line


def context(run_root) -> VerifyContext:
    """The context the pipeline builds for TASK's attempt RESULT."""
    return VerifyContext.of(TASK, run_root, RESULT.attempt_id)


def plan(monkeypatch, existing, probe_results, mode="whole-suite"):
    """plan_p2p with ``existing`` base tests and ``probe_results`` returned by successive probes;
    returns (plan or exception, the file sets probed)."""
    probed: list[tuple[str, ...]] = []
    results = iter(probe_results)

    def fake_probe(task, files, result, ctx, budget):
        probed.append(files)
        return next(results)

    monkeypatch.setattr(p2p, "_base_tests", lambda task, exclude: existing)
    monkeypatch.setattr(p2p, "_probe", fake_probe)
    try:
        return plan_p2p(TASK, (), (), mode, 30, RESULT, context("/runs")), probed
    except (P2PRejected, BaseUncomparable) as exc:
        return exc, probed


def uncomparable(got) -> bool:
    """#234: a failed base probe is no evidence (UNCOMPARABLE), never a P2PRejected refusal."""
    return (
        isinstance(got, BaseUncomparable)
        and not isinstance(got, P2PRejected)
        and got.comparison.kind == "UNCOMPARABLE"
        and got.comparison.code == "P2P_BASE_PROBE_UNUSABLE"
    )


def test_degraded_probe_with_an_empty_module_dir_selection_is_uncomparable(monkeypatch):
    # F1: nested tests that map to no changed file and no root-level tests: module-dir selects
    # nothing. After a whole-suite probe timeout that must not become "nothing to run".
    got, probed = plan(monkeypatch, ("tests/unit/test_x.py",), [(None, "base-probe-timeout")])
    assert uncomparable(got)
    assert "P2P_BASE_PROBE_UNUSABLE" in str(got) and "base-probe-timeout" in str(got)
    assert probed == [("tests/unit/test_x.py",)]


def test_degraded_probe_that_failed_with_an_empty_module_dir_selection_is_uncomparable(monkeypatch):
    got, _ = plan(monkeypatch, ("tests/unit/test_x.py",), [(None, "base-probe-failed")])
    assert uncomparable(got) and "base-probe-failed" in str(got)


def test_module_dir_chosen_directly_with_nothing_selected_runs_nothing(monkeypatch):
    got, probed = plan(monkeypatch, ("tests/unit/test_x.py",), [], mode="module-dir")
    assert isinstance(got, P2PPlan) and got.spec is None and probed == []
    assert got.rule["targets"] == 0 and got.rule["degraded"] == ""


def test_a_degraded_probe_records_the_rule_actually_used(monkeypatch):
    existing = ("tests/a/test_b.py", "tests/a/test_c.py", "tests/z/test_slow.py")
    outcomes = {"tests/a/test_b.py::test_1": "passed", "tests/a/test_c.py::test_2": "passed"}
    got, probed = plan(monkeypatch, existing, [(None, "base-probe-timeout"), (outcomes, "")])
    assert isinstance(got, P2PPlan) and got.spec is not None
    assert probed[1] == ("tests/a/test_b.py", "tests/a/test_c.py")  # same-package sibling too
    assert got.rule["configured"] == "whole-suite" and got.rule["applied"] == "module-dir"
    assert got.rule["degraded"] == "base-probe-timeout" and got.rule["targets"] == 2
    assert json.loads(got.note) == got.rule  # what the observation's artifact hash binds
    assert got.spec.verifier_id == "p2p-module-dir"


def test_degraded_probe_whose_module_dir_tests_all_fail_on_the_base_is_uncomparable(monkeypatch):
    # G1: module-dir selected files after the degradation, but none of their tests passes on
    # the base, so the target set is empty: still not "nothing to run"
    existing = ("tests/a/test_b.py", "tests/z/test_slow.py")
    outcomes = {"tests/a/test_b.py::test_1": "failed", "tests/a/test_b.py::test_2": "skipped"}
    got, probed = plan(monkeypatch, existing, [(None, "base-probe-timeout"), (outcomes, "")])
    assert uncomparable(got) and len(probed) == 2
    assert "P2P_BASE_PROBE_UNUSABLE" in str(got) and "passes on the base" in str(got)


def test_a_report_over_the_cap_degrades_to_module_dir_with_that_reason(monkeypatch):
    # #184: a suite too large for one guard report. The changed file maps to nothing, so
    # module-dir selects the tests directly in the root: the nested big file is left out.
    existing = ("tests/nested/test_big.py", "tests/test_calc.py")
    outcomes = {"tests/test_calc.py::test_a": "passed"}
    got, probed = plan(
        monkeypatch, existing, [(None, "base-probe-report-over-cap"), (outcomes, "")]
    )
    assert isinstance(got, P2PPlan) and got.spec is not None
    assert got.rule["degraded"] == "base-probe-report-over-cap"
    assert got.rule["applied"] == "module-dir" and got.rule["targets"] == 1
    assert probed == [existing, ("tests/test_calc.py",)]


def test_a_degraded_probe_that_would_run_the_same_files_is_uncomparable_without_a_second_probe(
    monkeypatch,
):
    # #184 flat layout: module-dir selects exactly the files whole-suite could not report on,
    # so probing again is pointless; the refusal names the cause, not a module-dir probe
    got, probed = plan(monkeypatch, ("tests/test_calc.py",), [(None, "base-probe-report-over-cap")])
    assert uncomparable(got) and probed == [("tests/test_calc.py",)]
    assert "P2P_BASE_PROBE_UNUSABLE" in str(got) and "base-probe-report-over-cap" in str(got)
    assert "module-dir selects the same files" in str(got)


def test_no_base_passing_test_without_degradation_runs_nothing(monkeypatch):
    outcomes = {"tests/test_a.py::test_1": "failed"}
    got, _ = plan(monkeypatch, ("tests/test_a.py",), [(outcomes, "")])
    assert isinstance(got, P2PPlan) and got.spec is None and got.rule["targets"] == 0


def test_tests_failing_or_skipped_on_the_base_are_left_out(monkeypatch):
    outcomes = {
        "tests/test_a.py::test_ok": "passed",
        "tests/test_a.py::test_bad": "failed",
        "tests/test_b.py::test_skip": "skipped",
        "tests/test_c.py::test_p[1]": "passed",
        "tests/test_c.py::test_p[2]": "failed",
    }
    existing = ("tests/test_a.py", "tests/test_b.py", "tests/test_c.py")
    got, _ = plan(monkeypatch, existing, [(outcomes, "")])
    assert isinstance(got, P2PPlan) and got.spec is not None
    assert got.spec.targets == ("tests/test_a.py::test_ok",)


@pytest.mark.parametrize(
    ("survivors", "mutated", "why"),
    [
        (1, (), "base-probe-survivors"),
        (-1, (), "base-probe-survivors"),  # the process list could not be read
        (0, ("junk.txt",), "base-probe-mutated"),
    ],
)
def test_an_unclean_base_probe_is_unusable(monkeypatch, tmp_path, survivors, mutated, why):
    # #285: the candidate run blocks on survivors or a mutated checkout, so a base run that
    # already has them gives no usable comparison (the candidate would carry the base's leftover)
    def fake_run_verifiers(*args, **kwargs):
        run = make_run(True, outcomes={"tests/test_a.py::test_a": "passed"}, log_dir=tmp_path)
        return (replace(run, survivors=survivors, mutated=mutated),)

    monkeypatch.setattr(p2p, "run_verifiers", fake_run_verifiers)
    assert p2p._probe(TASK, ("tests/test_a.py",), RESULT, context(tmp_path), 30) == (None, why)


@pytest.mark.parametrize("why", p2p.UNCLEAN)
@pytest.mark.parametrize("mode", ["whole-suite", "module-dir"])
def test_an_unclean_base_is_uncomparable_without_a_narrower_probe(monkeypatch, why, mode):
    # #285 (cross-model review): a narrower suite would be weaker evidence than the configured one
    existing = ("tests/nested/test_x.py", "tests/test_calc.py")
    got, probed = plan(monkeypatch, existing, [(None, why)], mode=mode)
    assert uncomparable(got) and f"{why} in {mode} mode" in str(got)
    assert len(probed) == 1


def test_an_unclean_degraded_probe_keeps_both_reasons(monkeypatch):
    existing = ("tests/nested/test_x.py", "tests/test_calc.py")
    got, probed = plan(
        monkeypatch, existing, [(None, "base-probe-timeout"), (None, p2p.UNCLEAN[0])]
    )
    assert uncomparable(got) and len(probed) == 2
    assert "base-probe-timeout; base-probe-survivors in module-dir mode" in str(got)


def test_targets_keep_the_probe_order(monkeypatch):
    # #287: never sorted; pytest runs node ids in the order given
    outcomes = {
        "tests/test_z.py::test_1": "passed",
        "tests/test_b.py::test_z": "passed",
        "tests/test_b.py::test_a": "passed",
        "tests/test_b.py::test_skip": "skipped",
        "tests/test_a.py::test_1": "passed",
    }
    existing = ("tests/test_a.py", "tests/test_b.py", "tests/test_z.py")
    got, _ = plan(monkeypatch, existing, [(outcomes, "")])
    assert isinstance(got, P2PPlan) and got.spec is not None
    assert got.spec.targets == (
        "tests/test_z.py",
        "tests/test_b.py::test_z",
        "tests/test_b.py::test_a",
        "tests/test_a.py",
    )


def test_a_clean_base_probe_returns_its_outcomes(monkeypatch, tmp_path):
    outcomes = {"tests/test_a.py::test_a": "passed"}
    run = make_run(True, outcomes=outcomes, log_dir=tmp_path)
    monkeypatch.setattr(p2p, "run_verifiers", lambda *a, **k: (run,))
    assert p2p._probe(TASK, ("tests/test_a.py",), RESULT, context(tmp_path), 30) == (outcomes, "")


# --- the candidate run: timeout and the flaky rule ------------------------------------------


def make_run(passed, *, timed_out=False, outcomes=None, log_dir, plain=None, mutated=()):
    log_dir.mkdir(parents=True, exist_ok=True)
    spec = VerifierSpec("p2p-whole-suite", ("tests/test_a.py",))
    return VerifierRun(
        spec,
        None if timed_out else (0 if passed else 1),
        timed_out,
        mutated,
        log_dir / "verifier.log",
        VerifierObservation(spec.verifier_id, "x/verifier.log", "sha256:0", passed),
        outcomes or {},
        plain_failure=(not passed and not timed_out) if plain is None else plain,
    )


def candidate(monkeypatch, tmp_path, runs, nodes=()):
    """run_plan with run_verifiers returning ``runs`` in turn and the plan binding ``nodes``;
    returns (result, notes seen)."""
    notes: list[str] = []
    queue = iter(runs)

    def fake_run_verifiers(*args, note="", **kwargs):
        notes.append(note)
        return (next(queue),)

    monkeypatch.setattr(p2p, "run_verifiers", fake_run_verifiers)
    rule = {"configured": "whole-suite", "applied": "whole-suite", "degraded": "", "targets": 1}
    spec = VerifierSpec("p2p-whole-suite", ("tests/test_a.py",))
    got = run_plan(P2PPlan(rule, spec, 30, nodes), TASK, "c" * 40, RESULT, context(tmp_path))
    return got, notes


BOUND = ("tests/test_a.py::t[1]", "tests/test_a.py::t[2]")


def test_a_passed_run_without_a_bound_node_id_is_not_a_pass_and_not_re_run(monkeypatch, tmp_path):
    first = make_run(True, outcomes={BOUND[0]: "passed"}, log_dir=tmp_path / "1")
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first], BOUND)
    assert len(notes) == 1 and not runs[0].observation.passed and "rerun" not in rule
    assert runs[0].observation.artifact_hash == first.observation.artifact_hash
    assert rule["missing"] == [BOUND[1]] and "did not run" in runs[0].why
    assert "missing" not in json.loads(notes[0])  # the note is hashed before the run


def test_a_run_with_every_bound_node_id_and_more_passes(monkeypatch, tmp_path):
    outcomes = dict.fromkeys((*BOUND, "tests/test_a.py::t[3]"), "passed")
    first = make_run(True, outcomes=outcomes, log_dir=tmp_path / "1")
    (runs, rule), _ = candidate(monkeypatch, tmp_path, [first], BOUND)
    assert runs == (first,) and "missing" not in rule


def test_a_bound_candidate_timeout_is_handled_as_before(monkeypatch, tmp_path):
    # the budget is per run, as before: a timeout is not a pass, not re-run, nothing "missing"
    first = make_run(False, timed_out=True, log_dir=tmp_path / "1")
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first], BOUND)
    assert runs == (first,) and len(notes) == 1 and "missing" not in rule and "rerun" not in rule


def test_a_re_run_without_a_bound_node_id_blocks(monkeypatch, tmp_path):
    first = make_run(False, outcomes={BOUND[0]: "failed"}, log_dir=tmp_path / "1")
    second = make_run(True, outcomes={BOUND[0]: "passed"}, log_dir=tmp_path / "2")
    (runs, rule), _ = candidate(monkeypatch, tmp_path, [first, second], BOUND)
    assert not runs[0].observation.passed and rule["p2p_rerun_passed"] is False
    assert rule["missing"] == [BOUND[1]]


def test_a_passing_candidate_run_is_not_re_run(monkeypatch, tmp_path):
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [make_run(True, log_dir=tmp_path / "1")])
    assert len(notes) == 1 and runs[0].observation.passed and "rerun" not in rule
    assert "p2p_rerun_passed" not in rule  # a clean pass carries no flag (#174)


def test_a_candidate_timeout_is_never_verified_and_not_re_run(monkeypatch, tmp_path):
    first = make_run(False, timed_out=True, log_dir=tmp_path / "1")
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first])
    assert len(notes) == 1 and not runs[0].observation.passed and "rerun" not in rule


def test_a_failure_that_passes_on_the_re_run_is_not_blocking_but_recorded(monkeypatch, tmp_path):
    first = make_run(
        False, outcomes={"tests/test_a.py::t": "failed"}, log_dir=tmp_path / "1"
    )  # fmt: skip
    second = make_run(True, log_dir=tmp_path / "2")
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first, second])
    assert runs == (second,)  # Core sees the re-run only
    assert rule["rerun"] == {
        "failed": ["tests/test_a.py::t"],
        "returncode": 1,
        "timed_out": False,
        "mutated": [],
        "survivors": 0,
        "ran": False,
    }
    assert rule["p2p_rerun_passed"] is True  # #174: told apart from a clean pass
    bound = {k: v for k, v in rule.items() if k != "p2p_rerun_passed"}
    assert json.loads(notes[1]) == bound  # the re-run's artifact hash binds the flaky record
    assert json.loads((tmp_path / "2" / "p2p-selection.json").read_text()) == bound


def test_a_failure_on_both_runs_blocks(monkeypatch, tmp_path):
    first = make_run(False, outcomes={"tests/test_a.py::t": "failed"}, log_dir=tmp_path / "1")
    second = make_run(False, outcomes={"tests/test_a.py::t": "failed"}, log_dir=tmp_path / "2")
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first, second])
    assert runs == (second,) and not runs[0].observation.passed
    assert len(notes) == 2 and rule["rerun"]["failed"] == ["tests/test_a.py::t"]
    assert rule["p2p_rerun_passed"] is False


def test_failing_test_names_in_the_record_are_sanitised(monkeypatch, tmp_path):
    name = "tests/test_a.py::t\x1b[31m\n" + "x" * 500
    first = make_run(False, outcomes={name: "failed"}, log_dir=tmp_path / "1")
    second = make_run(True, log_dir=tmp_path / "2")
    (_, rule), _ = candidate(monkeypatch, tmp_path, [first, second])
    (failed,) = rule["rerun"]["failed"]
    assert failed.isprintable() and len(failed) == 200 and failed.startswith("tests/test_a.py::t?")


@pytest.mark.parametrize(
    "signal",
    [{"mutated": ("src/x.py",)}, {"survivors": 1}, {}],  # {}: oversized log / no guard report
)
def test_a_failure_that_is_not_a_plain_test_failure_blocks_without_a_re_run(
    monkeypatch, tmp_path, signal
):
    # F2: integrity signals (mutated checkout, surviving process, oversized log, the guard says
    # the run did not happen) are never given a second try
    first = make_run(
        False, outcomes={"tests/test_a.py::t": "passed"}, log_dir=tmp_path / "1",
        plain=False, mutated=signal.get("mutated", ()),
    )  # fmt: skip
    if "survivors" in signal:
        first = replace(first, survivors=signal["survivors"])
    (runs, rule), notes = candidate(monkeypatch, tmp_path, [first])
    assert len(notes) == 1 and runs == (first,) and "rerun" not in rule


def test_a_re_run_that_times_out_blocks(monkeypatch, tmp_path):
    first = make_run(False, outcomes={"tests/test_a.py::t": "failed"}, log_dir=tmp_path / "1")
    second = make_run(False, timed_out=True, log_dir=tmp_path / "2")
    (runs, _), _ = candidate(monkeypatch, tmp_path, [first, second])
    assert not runs[0].observation.passed


def test_no_plan_spec_runs_nothing(tmp_path):
    rule = {"applied": "off"}
    assert run_plan(P2PPlan(rule, None), TASK, "c" * 40, RESULT, context(tmp_path)) == (
        (),
        rule,
    )


@pytest.mark.parametrize("mode", ["off"])
def test_mode_off_never_probes(monkeypatch, mode):
    got, probed = plan(monkeypatch, ("tests/test_a.py",), [], mode=mode)
    assert isinstance(got, P2PPlan) and got.spec is None and probed == []


@pytest.mark.parametrize("unreadable", ["base", "test commit"])
def test_a_changed_test_file_that_cannot_be_read_is_refused_not_dropped(monkeypatch, unreadable):
    """The file exists on both commits (test_paths: A or M, and on the base): a failed read is
    a refusal, never an empty file whose base tests would all count as changed."""
    base, test_commit = "b" * 40, "c" * 40
    gone = base if unreadable == "base" else test_commit

    def fake_git(repo, *args):
        if args[1].startswith(gone):
            raise GitCommandError("git show failed")
        return "def test_a():\n    assert True\n"

    monkeypatch.setattr(deferred_tests, "run_git", fake_git)
    with pytest.raises(deferred_tests.TestCommitRejected) as caught:
        changed_tests(Path("/repo"), base, test_commit, ("tests/test_a.py",))
    assert caught.value.code == "TEST_COMMIT_UNREADABLE"
