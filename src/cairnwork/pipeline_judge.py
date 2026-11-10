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

from cairnwork import candidate_env, failure_evidence, protection
from cairnwork.account_check import unusable
from cairnwork.account_prelude import AccountUnusableBeforeRun, after_candidate_ran, busy_once_more
from cairnwork.blackbox_judge import judge_blackbox
from cairnwork.candidate_sys_path import refuse_new_sys_path_mutation
from cairnwork.changeset import ChangeSetError, read_change_set
from cairnwork.collect_import import as_failed
from cairnwork.conftest_check import check_conftests, without_conftests
from cairnwork.core_request import AcceptanceSpec, VerifierObservation
from cairnwork.deferred_overlay import stray_deletions
from cairnwork.deferred_store import consume
from cairnwork.deferred_tests import (
    BaseUncomparable,
    Comparison,
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
from cairnwork.p2p_nodes import clean
from cairnwork.pytest_config import PytestConfigError, gate_import_paths, test_file_rule
from cairnwork.reasons import explain, verdict_reason
from cairnwork.verifier_account import VerifierAccountError
from cairnwork.verifier_spec import protected_verifier_inputs
from cairnwork.verifiers import run_verifiers
from cairnwork.verify_context import VerifyContext, verifier_interpreter
from cairnwork.workspace import CandidateRejected


def second_seed(seed: str) -> str:
    """Return the next uint32 seed, so a base replay is deterministic from the attempt record."""
    if not seed.isdecimal() or int(seed) > 4294967295:
        raise ValueError(f"hash seed not a uint32: {seed!r}")
    return str((int(seed) + 1) % 4294967296)


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


def _sealing_with_tests(task, ctx: VerifyContext, candidate: str) -> None:
    """#386: intake judged sealed packages (#382) without the hidden test commit, which it does
    not fetch. Now that the commit is here, judge the candidate's own paths again with it: a test
    that puts a sealed package on sys.path must not meet a candidate file that shadows a module
    there. Fails closed when the change or the import paths cannot be read."""
    try:
        paths = tuple(read_change_set(task.repo, task.base_commit, candidate).paths)
        roots = gate_import_paths(task.repo, task.base_commit, task.import_paths)
        protected = protected_verifier_inputs(
            paths,
            task.test_roots,
            repo=task.repo,
            base_commit=task.base_commit,
            python=verifier_interpreter(ctx),
            import_paths=roots,
            allow_deletions=task.allow_deletions,
            test_commit=task.test_commit,
        )
    except (ChangeSetError, PytestConfigError) as exc:
        raise TestCommitRejected("TEST_COMMIT_UNSEALS_CANDIDATE", str(exc)) from exc
    if protected:
        raise TestCommitRejected("TEST_COMMIT_UNSEALS_CANDIDATE", ", ".join(protected))


def tests_with_teeth(
    task, specs, result, ctx: VerifyContext, candidate: str | None = None
) -> tuple[str, ...]:
    """Check a task's deferred tests once the worker is done: plain test files on the
    base whose test cases really fail there. Returns their paths; raises TestCommitRejected
    when they do not (NO_TEETH), leave a test file's changes out of every verifier target
    (DEFERRED_TESTS_NOT_TARGETED) or were not all collected and run by the base run
    (DEFERRED_TESTS_NOT_RUN, #660), and BaseUncomparable when the base cannot tell (#234).
    Callers judging a candidate pass it, so its paths meet the fetched tests (#386)."""
    check_roots_on_base(task.repo, task.base_commit, task.test_roots)  # before any base run
    ensure_test_commit(task.repo, task.test_commit, task.test_store)
    if candidate is not None:
        _sealing_with_tests(task, ctx, candidate)
    paths, deleted = test_paths(task.repo, task.base_commit, task.test_commit, task.test_roots)
    targets = new_tests(task.repo, task.base_commit, task.test_commit, paths)  # may refuse
    check_conftests(task.repo, task.base_commit, task.test_commit, paths)  # #798
    named = (t for spec in specs for t in spec.targets)
    if missing := untargeted(without_conftests(paths), targets, named):
        hint = (
            "name each such file, or each of its new or changed tests, as a verifier target; "
            "改了模組層或類別本體，請點名整個檔案"
        )
        raise TestCommitRejected("DEFERRED_TESTS_NOT_TARGETED", f"{', '.join(missing)}; {hint}")
    # #796: a base run goes on after a collect error (never a collect-only spec): the other files
    # still run, and a module-level import of a name the fix adds can be told from other errors
    base = tuple(
        s if s.collect_only else replace(s, continue_on_collect_errors=True) for s in specs
    )
    runs = run_verifiers(ctx, task.test_commit, base, f"{result.attempt_id}-b", deny=True)
    recorded_runs = runs
    # #1034: what the base run really ran, before as_failed adds function-level ids for a #796 file
    nodes = {r.spec.verifier_id: tuple(r.outcomes) for r in runs if not r.spec.collect_only}

    def is_test(path: str) -> bool:  # asked only when a record qualifies: a base test file, or
        # any file under a test root (a test package's own helper is not code the fix adds)
        roots = tuple(f"{r}/" for r in task.test_roots)
        return path.startswith(roots) or test_file_rule(task.repo, task.base_commit)(path)

    runs = [as_failed(run, targets, is_test) for run in runs]  # before the base comparison
    comparison = base_comparison(runs, targets)  # decided before the base logs go
    if comparison.kind == "UNCOMPARABLE":
        result.failure_evidence.extend(failure_evidence.capture_for_task(recorded_runs, task, ctx))
    for run in runs:  # #797 step 1: recorded only, no verdict reads it yet (deny_channel.py)
        if run.deny_evidence:
            result.logs.append(f"base deny evidence {run.spec.verifier_id}: {run.deny_evidence}")
    for run in runs:  # not evidence for Core; would show the tests to later workers
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    if comparison.kind == "UNCOMPARABLE":
        raise BaseUncomparable(comparison)
    if comparison.kind != "TEETH":
        raise TestCommitRejected(comparison.code, comparison.detail)
    _check_seed_replay(ctx, result, runs, targets, base, task.test_commit, is_test)
    return TestFiles(paths, deleted, nodes)


def _check_seed_replay(ctx, result, runs, targets, base, test_commit, is_test) -> None:
    """A second base run must independently show the same teeth under a distinct seed."""
    try:
        replay_seed = second_seed(ctx.hash_seed)
    except ValueError as exc:
        detail = f"hash seed not a uint32: {ctx.hash_seed!r}"
        raise BaseUncomparable(Comparison("UNCOMPARABLE", "BASE_SEED_SENSITIVE", detail)) from exc
    second_ctx = replace(ctx, hash_seed=replay_seed)
    second_runs = run_verifiers(second_ctx, test_commit, base, f"{result.attempt_id}-b2", deny=True)
    second_runs = [as_failed(run, targets, is_test) for run in second_runs]
    second_comparison = base_comparison(second_runs, targets)
    for run in second_runs:
        if run.deny_evidence:
            result.logs.append(
                f"second base deny evidence {result.attempt_id}-b2/{run.spec.verifier_id} "
                f"(seed {second_ctx.hash_seed}): {run.deny_evidence}"
            )
    for run in second_runs:
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)
    first_failed = _failed_targets(runs, targets)
    second_failed = _failed_targets(second_runs, targets)
    if second_comparison.kind != "TEETH" or first_failed != second_failed:
        changed = sorted(first_failed ^ second_failed)
        detail = _seed_sensitive_detail(
            changed,
            first_failed,
            second_failed,
            ctx.hash_seed,
            second_ctx.hash_seed,
            second_comparison,
        )
        raise BaseUncomparable(Comparison("UNCOMPARABLE", "BASE_SEED_SENSITIVE", detail))


def _failed_targets(runs, targets: frozenset[str]) -> frozenset[str]:
    """Failed full node IDs whose unparameterized target is in the deferred test set."""
    target_prefixes = {target.split("[", 1)[0] for target in targets}
    return frozenset(
        node
        for run in runs
        for node, outcome in run.outcomes.items()
        if outcome == "failed" and node.split("[", 1)[0] in target_prefixes
    )


def _seed_sensitive_detail(
    changed, first_failed, second_failed, first_seed, second_seed, comparison
):
    """Bound candidate-controlled node IDs before placing them in a refusal detail."""
    ids = sorted(set(changed) | set(first_failed) | set(second_failed))[:20]
    quoted = [_quoted_node_id(node) for node in ids]
    detail = (
        f"failed node IDs differ or second run was invalid at seeds "
        f"seed {first_seed} and seed {second_seed}: {quoted}"
    )
    if comparison.kind != "TEETH":
        detail += f"; second run: {comparison.kind} {comparison.code}"
    return detail


def _quoted_node_id(node: str) -> str:
    """Return a repr-quoted node ID no longer than 200 characters."""
    candidate = node
    while len(repr(candidate)) > 200:
        candidate = candidate[: max(0, len(candidate) - (len(repr(candidate)) - 199))]
    return repr(candidate)


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
    control: bool = False,
) -> None:
    """Verify result.candidate_commit and have Core notarise it (sets verified, reason, logs);
    shared by the worker pipeline and ``verify_candidate``; reach(phase) records progress. Verify
    only: a ``binding`` (#229) binds the verdict to the tree that ran and the binding's head;
    ``unless_passed`` (#259), a failed base check, is raised before Core if a verifier did not
    pass; ``control`` (#962 2b): a failure of setup denials the unchanged tree reproduces is
    UNCOMPARABLE. Core is not asked when all passed but a new test is in no run's record (#697).
    #1065: candidate_sys_path refuses a shadowing import edit before any verifier runs."""
    _refuse_candidate_sys_path(task, result)
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
    result.failure_evidence = failure_evidence.capture_for_task(runs, task, ctx)
    if unless_passed is not None and not all(run.observation.passed for run in runs):
        raise unless_passed
    missing = _not_run_here(task, verified_by, test_files)
    if refusal := _not_run_refusal(missing, test_files, verified_by, (*runs, *checks)):
        result.reason = refusal  # #697, #1034: Core is not asked
        return
    # Core's ALLOW is whole-contract; only the test commit's files can be deleted (producer check)
    if deleted_of(test_files) and not task.allow_deletions:
        if stray := stray_deletions(task.repo, task.base_commit, judged, deleted_of(test_files)):
            result.reason = f"DELETION_OUTSIDE_TEST_COMMIT: {stray[:5]}"
            return
    ids, observations, gate_note = _contract(task, runs, checks, declared, binding, result)
    if control and not missing:  # #1030 r1 nit: a missing deferred test is reported as is
        candidate_env.decide(task, verified_by, observations, ctx, result)
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


def _refuse_candidate_sys_path(task, result) -> None:
    """Keep the pre-verification import scan bound to the candidate and its deferred tests."""
    refuse_new_sys_path_mutation(
        task.repo,
        task.base_commit,
        result.candidate_commit,
        task.allowed_files,
        task.test_commit or None,
    )


def _not_run_here(task, runs, test_files) -> list[str]:
    """Every new or changed test must be in the verification run's guard record too (#697), as
    in the base run's (base_comparison, #660)."""
    if not (task.test_commit and test_files):
        return []
    return not_run(runs, new_tests(task.repo, task.base_commit, task.test_commit, test_files))


def _not_run_refusal(missing, test_files, verified_by, judged) -> str:
    """When every run and check in ``judged`` passed, the refusal for a test no run collected:
    a new test missing from every verifier's record (#697: a whole-file target passes on another
    test of that file), or a test the base run of a verifier ran (any outcome) that the
    candidate's run of it did not (#1034: e.g. dropped by collection-time project code the
    candidate changed). "" otherwise."""
    if not all(item.observation.passed for item in judged):
        return ""
    if missing:
        return f"DEFERRED_TESTS_NOT_RUN_IN_VERIFICATION: no verifier collected or ran {missing}"
    gone = _base_nodes_gone(test_files, verified_by)
    if not gone:
        return ""
    shown = ", ".join(map(clean, gone[:10])) + (f", +{len(gone) - 10} more" if gone[10:] else "")
    head = f"NAMED_TESTS_NOT_RUN: {len(gone)} test(s) the base run ran did not run on the candidate"
    return f"{head}: {shown}"


def _base_nodes_gone(test_files, runs) -> list[str]:
    """Full node ids (never cut at ``[``) the base run of a verifier recorded and the candidate's
    run of that verifier did not (#1034). A verifier with no base record adds nothing."""
    base = getattr(test_files, "base_nodes", {})
    return sorted(
        node
        for run in runs
        for node in base.get(run.spec.verifier_id, ())
        if node not in run.outcomes
    )


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
    except CandidateRejected as exc:  # #1065: the candidate's own files, before any verifier ran
        result.verified, result.reason = False, str(exc)
    except AccountUnusableBeforeRun as exc:
        if worker.isolated is not True:
            raise VerifierAccountError(exc.detail) from exc
        found = unusable(exc.detail).comparison
        result.verified, result.uncomparable = False, found.code
        result.reason = found.worker_reason(isolated=True)
