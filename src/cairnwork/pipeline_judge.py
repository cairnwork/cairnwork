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
from dataclasses import replace
from functools import partial
from pathlib import Path

from cairnwork import protection
from cairnwork.account_check import unusable
from cairnwork.account_prelude import AccountUnusableBeforeRun, after_candidate_ran, busy_once_more
from cairnwork.blackbox_judge import judge_blackbox
from cairnwork.collect_import import as_failed
from cairnwork.conftest_check import check_conftests, without_conftests
from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.deferred_overlay import stray_deletions
from cairnwork.deferred_store import consume
from cairnwork.deferred_tests import (
    BaseUncomparable,
    TestCommitRejected,
    TestFiles,
    base_comparison,
    check_roots_on_base,
    deleted_of,
    ensure_test_commit,
    never_seen,
    new_tests,
    not_run,
    test_paths,
    untargeted,
)
from cairnwork.p2p import P2PPlan, run_plan
from cairnwork.pytest_config import test_file_rule
from cairnwork.reasons import explain, verdict_reason
from cairnwork.verifier_account import VerifierAccountError
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
    when they do not (NO_TEETH), leave a test file's changes out of every verifier target
    (DEFERRED_TESTS_NOT_TARGETED) or were not all collected and run by the base run
    (DEFERRED_TESTS_NOT_RUN, #660), and BaseUncomparable when the base cannot tell (#234)."""
    check_roots_on_base(task.repo, task.base_commit, task.test_roots)  # before any base run
    ensure_test_commit(task.repo, task.test_commit, task.test_store)
    paths, deleted = test_paths(task.repo, task.base_commit, task.test_commit, task.test_roots)
    targets = new_tests(task.repo, task.base_commit, task.test_commit, paths)  # may refuse
    check_conftests(task.repo, task.base_commit, task.test_commit, paths)  # #798
    named = (t for spec in specs for t in spec.targets)
    if missing := untargeted(without_conftests(paths), targets, named):
        hint = "name each such file, or each of its new or changed tests, as a verifier target"
        raise TestCommitRejected("DEFERRED_TESTS_NOT_TARGETED", f"{', '.join(missing)}; {hint}")
    # #796: a base run goes on after a collect error (never a collect-only spec): the other files
    # still run, and a module-level import of a name the fix adds can be told from other errors
    base = tuple(
        s if s.collect_only else replace(s, continue_on_collect_errors=True) for s in specs
    )
    runs = run_verifiers(ctx, task.test_commit, base, f"{result.attempt_id}-b", deny=True)

    def is_test(path: str) -> bool:  # asked only when a record qualifies: a base test file, or
        # any file under a test root (a test package's own helper is not code the fix adds)
        roots = tuple(f"{r}/" for r in task.test_roots)
        return path.startswith(roots) or test_file_rule(task.repo, task.base_commit)(path)

    runs = [as_failed(run, targets, is_test) for run in runs]  # before the base comparison
    comparison = base_comparison(runs, targets)  # decided before the base logs go
    for run in runs:  # #797 step 1: recorded only, no verdict reads it yet (deny_channel.py)
        if run.deny_evidence:
            result.logs.append(f"base deny evidence {run.spec.verifier_id}: {run.deny_evidence}")
    for run in runs:  # not evidence for Core; would show the tests to later workers
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if comparison.kind == "UNCOMPARABLE":
        raise BaseUncomparable(comparison)
    if comparison.kind != "TEETH":
        raise TestCommitRejected(comparison.code, comparison.detail)
    return TestFiles(paths, deleted)


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
    asked when a verifier did not pass; when they all passed the candidate's own evidence stands.
    When every run and check passed but a new test of the test commit is in no verifier run's
    record, Core is not asked either (DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION, #697)."""
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
    # #803: no candidate code ran as the account before this run unless a blackbox run (verify
    # only, which judges any account error the same) came first: then a refusal is a plain error
    first = run_verifiers if task.blackbox is None else partial(after_candidate_ran, run_verifiers)
    runs = busy_once_more(first, ctx, judged, specs, f"{result.attempt_id}-v")
    verified_by = list(runs)  # the verifier runs alone, as the base run's check reads them
    if p2p is not None:
        p2p_runs, result.p2p = after_candidate_ran(run_plan, p2p, task, judged, result, ctx)
        runs += p2p_runs
    # keep what came before, e.g. a failed base probe's tail (#331)
    result.logs = [*result.logs, *(str(run.log_path) for run in runs), *bb_logs]
    if unless_passed is not None and not all(run.observation.passed for run in runs):
        raise unless_passed
    if (missing := _not_run_here(task, verified_by, test_files)) and all(
        o.passed for o in (*(run.observation for run in runs), *(c.observation for c in checks))
    ):  # #697: a whole-file target passes on another test of that file; Core is not asked
        result.reason = (
            f"DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION: no verifier collected or ran {missing}"
        )
        return
    # Core's ALLOW is whole-contract; only the test commit's files can be deleted (producer check)
    if deleted_of(test_files) and not task.allow_deletions:
        if stray := stray_deletions(task.repo, task.base_commit, judged, deleted_of(test_files)):
            result.reason = f"DELETION_OUTSIDE_TEST_COMMIT: {stray[:5]}"
            return
    ids, observations, gate_note = _contract(task, runs, checks, declared, binding, result)
    spec = AcceptanceSpec(
        task_id,
        digest,
        ids,
        tuple(task.allowed_files) + test_files + deleted_of(test_files),
        task.allow_deletions or bool(deleted_of(test_files)),
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


def _not_run_here(task, runs, test_files) -> list[str]:
    """Every new or changed test must be in the verification run's guard record too (#697), as
    in the base run's (base_comparison, #660)."""
    if not (task.test_commit and test_files):
        return []
    return not_run(runs, new_tests(task.repo, task.base_commit, task.test_commit, test_files))


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


def judge_for(worker, task, specs, digest, notary, result, *args) -> None:
    """``judge_candidate`` for the worker pipeline: a prelude refusal before any candidate code ran
    as the account is no evidence for an isolated worker (UNCOMPARABLE, #803); otherwise the
    infrastructure error it was."""
    try:
        judge_candidate(task, specs, digest, notary, result, *args)
    except AccountUnusableBeforeRun as exc:
        if worker.isolated is not True:
            raise VerifierAccountError(exc.detail) from exc
        found = unusable(exc.detail).comparison
        result.verified, result.uncomparable = False, found.code
        result.reason = found.worker_reason(isolated=True)
