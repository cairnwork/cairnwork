"""The candidate-judging phase of one attempt, split out of pipeline.py for the 400-line cap.

``tests_with_teeth`` checks a task's deferred tests really fail on the base; ``judge_candidate``
runs the verifiers over the candidate and has Core notarise it. Both take everything they need as
arguments (no module-level state) and are called by ``pipeline._attempt``; ``judge_candidate`` is
also shared with ``verify_candidate``. They never call back into ``pipeline``, so there is no cycle.
``judge_candidate`` also runs a task's blackbox checks (#303, blackbox.py) when it declares any,
first (#311).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cairnwork import protection
from cairnwork.blackbox_judge import judge_blackbox
from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.deferred_store import consume
from cairnwork.deferred_tests import (
    BaseUncomparable,
    TestCommitRejected,
    base_comparison,
    check_roots_on_base,
    ensure_test_commit,
    never_seen,
    new_tests,
    test_paths,
    untargeted,
)
from cairnwork.p2p import P2PPlan, run_plan
from cairnwork.reasons import explain, verdict_reason
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext


def tests_unused(task, *, claim: bool) -> None:
    """A worker run's half of the single-use rule (deferred_store.consume). Before the worker:
    the test commit is on no clone here and no run, verify or worker, has used it. Right before
    the fetch (claim=True) this run registers it, so of two runs racing for one commit only one
    gets it. The store is checked before the worker too, as verify does (#332/#337/#339): an
    unimported commit or an unsafe store is refused while nothing is recorded yet. A TaskSpec
    with no store (library use, no state directory) keeps the mirror check."""
    if not task.test_commit:
        return
    if not claim:
        never_seen(task.repo, task.test_commit)
    if task.test_store is not None:
        consume(task.test_store.used, task.test_commit, record=claim)
        if not claim:
            task.test_store.require_imported(task.test_commit)


def tests_with_teeth(task, specs, result, ctx: VerifyContext) -> tuple[str, ...]:
    """Check a task's deferred tests once the worker is done: plain test files on the
    base whose test cases really fail there. Returns their paths; raises TestCommitRejected
    when they do not (NO_TEETH) or leave a test file's changes out of every verifier target
    (DEFERRED_TESTS_NOT_TARGETED), and BaseUncomparable when the base cannot tell (#234)."""
    check_roots_on_base(task.repo, task.base_commit, task.test_roots)  # before any base run
    ensure_test_commit(task.repo, task.test_commit, task.test_store)
    paths = test_paths(task.repo, task.base_commit, task.test_commit, task.test_roots)
    targets = new_tests(task.repo, task.base_commit, task.test_commit, paths)  # may refuse
    if missing := untargeted(paths, targets, (t for spec in specs for t in spec.targets)):
        hint = "name each such file, or each of its new or changed tests, as a verifier target"
        raise TestCommitRejected("DEFERRED_TESTS_NOT_TARGETED", f"{', '.join(missing)}; {hint}")
    runs = run_verifiers(ctx, task.test_commit, specs, f"{result.attempt_id}-b")
    comparison = base_comparison(runs, targets)  # decided before the base logs go
    for run in runs:  # not evidence for Core; would show the tests to later workers
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if comparison.kind == "UNCOMPARABLE":
        raise BaseUncomparable(comparison)
    if comparison.kind != "TEETH":
        raise TestCommitRejected(comparison.code, comparison.detail)
    return paths


def judge_candidate(
    task,
    specs,
    digest,
    notary,
    result,
    test_files,
    ctx: VerifyContext,
    reach,
    p2p: P2PPlan | None = None,
    binding: dict | None = None,
    unless_passed: BaseUncomparable | None = None,
) -> None:
    """Verify result.candidate_commit and have Core notarise it; sets result.verified,
    result.reason and result.logs. Shared by the worker pipeline and ``verify_candidate``;
    reach(phase) records progress. A binding (verify_candidate only, #229) binds the verdict to
    the tree that ran and to the binding's head instead of to result.candidate_commit.
    ``unless_passed`` (verify_candidate only, #259): a failed base check, raised before Core is
    asked when a verifier did not pass; when they all passed the candidate's own evidence stands."""
    judged = result.candidate_commit
    task_id = result.task_id
    reach("VERIFYING")
    checks, subject, bb_logs = (), binding["head"] if binding else judged, []
    declared = task.blackbox.checks if task.blackbox is not None else ()
    if task.blackbox is not None:  # #303: each check is one more observation Core requires;
        # it runs the candidate alone, without deferred tests that could hold the answers, and
        # before any other run of the candidate in this verify (#311: nothing the candidate saw in
        # a pytest phase of this verify can be waiting for it there)
        checks, log = judge_blackbox(task.blackbox, ctx, subject, f"{result.attempt_id}-v")
        bb_logs = [str(log)] if log else []
    runs = run_verifiers(ctx, judged, specs, f"{result.attempt_id}-v")
    if p2p is not None:
        p2p_runs, result.p2p = run_plan(p2p, task, judged, result, ctx)
        runs += p2p_runs
    # keep what came before, e.g. a failed base probe's tail (#331)
    result.logs = [*result.logs, *(str(run.log_path) for run in runs), *bb_logs]
    if unless_passed is not None and not all(run.observation.passed for run in runs):
        raise unless_passed
    ids, observations, gate_note = _contract(task, runs, checks, declared, binding, result)
    spec = AcceptanceSpec(
        task_id, digest, ids, tuple(task.allowed_files) + test_files, task.allow_deletions
    )
    reach("NOTARIZING")
    verdict = notary.notarize(
        task_id,
        result.attempt_id,
        spec,
        task.repo,
        task.base_commit,
        judged,
        observations,
        binding,
    )
    recorded = notary.verdict(task_id, result.attempt_id, expected_candidate=subject)
    result.verified = bool(verdict.verified and recorded and recorded.verified)
    result.reason = verdict_reason(result.verified, verdict, recorded) + explain(runs)
    result.reason += "".join(f" [{check.reason}]" for check in checks) + gate_note


def _contract(task, runs, checks, declared, binding, result):
    """The verifier ids Core requires and the observations it gets. The ids come from the task, not
    the results: a declared check that produced nothing is still required. Sets result.protection
    (protection.py), which a receipt binding must already name. In strict mode one more required
    observation, ``protection``, passes only when that level reaches the task's minimum."""
    ids = tuple(run.spec.verifier_id for run in runs) + tuple(c.verifier_id for c in declared)
    observations = tuple(run.observation for run in runs) + tuple(c.observation for c in checks)
    result.protection = protection.level(ids)
    if binding is not None and binding.get("protection", protection.NONE) != result.protection:
        raise RuntimeError(f"the binding names another protection level than {result.protection}")
    minimum, note = task.require_protection, ""
    if minimum:
        passed, artifact, digest = protection.gate(result.protection, minimum)
        ids += (protection.GATE,)
        observations += (VerifierObservation(protection.GATE, artifact, digest, passed),)
        note = f" [{protection.reason(result.protection, minimum)}]"
    return ids, observations, note
