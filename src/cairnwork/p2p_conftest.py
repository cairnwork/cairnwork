"""#798 half 2: the differential base probe for a test commit that adds or changes a conftest.

A conftest can change which base tests run or pass, and its import-time effects reach the whole
test process. So the differential probe runs the way P2P really runs: the same set of files (every
base test file under the test roots, in either mode: #915) in one process, once on the BASE and
once on the base with the WHOLE test commit laid over it. Every base test the test commit does
not change, add or remove must keep its outcome, else the test commit is refused
(``TEST_COMMIT_CONFTEST_CHANGES_BASE``: the test author's error, not the candidate's).

The comparison is only made when it can be trusted, else ``TEST_COMMIT_CONFTEST_UNCHECKED``, never
"equal": P2P is on (not ``off``), there is a base test file, and the run with the test commit was
clean within the time budget, collection finished with no collection error or skip, and every
collected id has an outcome.

The claim is narrow and stated: base outcomes are unchanged by the conftest. Behaviour that
depends on the candidate's code is trusted test-author code, as any test file is. The static check
(conftest_check.py) is hygiene, not the guard: names imported or rebound can carry behaviour it
cannot see; the probe is what holds. A receiver can re-run the static check but not this probe:
that part is the signer's word.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cairnwork.conftest_check import conftest_of
from cairnwork.deferred_tests import TestCommitRejected, deleted_of, lay_tests_over
from cairnwork.node_ids import unmatched_note
from cairnwork.p2p_nodes import clean
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

CHANGES_BASE = "TEST_COMMIT_CONFTEST_CHANGES_BASE"
UNCHECKED = "TEST_COMMIT_CONFTEST_UNCHECKED"
# #891, #915: with a conftest, a probe that cannot run cannot narrow (see conftest_files)
GOVERNS = "; with a test-commit conftest they are every base test file"


def conftest_files(task, test_files, mode, base_tests) -> tuple[str, ...]:
    """The files the P2P probe must run when the test commit has a conftest: every base test file
    under the test roots, in either mode (#915: a conftest's import-time effects reach the whole
    test process, so a base test outside the module-dir selection and the conftest's directory
    is reached too). () without a conftest; refused UNCHECKED when P2P is off or there is no base
    test file at all.

    #891, decided, and #915: no narrower fallback. When the probe of these files cannot be used
    (timeout, cap), a whole-suite task's module-dir fallback selects the same files and a
    module-dir task has none, so the task is UNCOMPARABLE (``P2P_BASE_PROBE_UNUSABLE``, detail
    ends with ``GOVERNS``): it fails closed. A narrower set would leave the conftest's effect on
    the other base tests unchecked."""
    conftests = conftest_of(test_files)
    if not conftests:
        return ()
    if mode == "off":
        raise TestCommitRejected(
            UNCHECKED, "p2p_selection is off: nothing would check the conftest"
        )
    existing = tuple(sorted(base_tests(task, frozenset(deleted_of(test_files)))))
    if not existing:
        raise TestCommitRejected(UNCHECKED, f"{', '.join(conftests)}: no base test file to compare")
    return existing


def check_overlay(task, test_files, files, outcomes, side, result, ctx, budget) -> None:
    """The differential probe, the way P2P really runs: the same ``files`` in one process on the
    base (``outcomes``, the plain probe) and on the base with the WHOLE test commit laid over it.
    Each base test the test commit does not change, add or remove (``side.changed``), nor sits in a
    file whose module-level code it changes (``side.probe_only``; P2P still runs those), must keep
    its outcome; else TEST_COMMIT_CONFTEST_CHANGES_BASE. Does nothing without a conftest."""
    if not conftest_of(test_files):
        return
    over = lay_tests_over(task.repo, task.base_commit, task.test_commit, test_files,
                          f"{result.attempt_id}-cf", Path(ctx.run_root))  # fmt: skip
    after, why = _side(ctx, over, files, result, budget, "o")
    if why:
        raise TestCommitRejected(UNCHECKED, f"the probe with the test commit is unusable: {why}")
    skip = side.changed | side.probe_only
    kept = {n: o for n, o in outcomes.items() if n.split("[", 1)[0] not in skip}
    differ = sorted(n for n, o in kept.items() if after.get(n) != o)
    if differ:
        shown = ", ".join(map(clean, differ[:5])) + (" ..." if len(differ) > 5 else "")
        note = unmatched_note(differ, after)
        detail = f"{len(differ)} base test(s) differ: {shown}"
        raise TestCommitRejected(CHANGES_BASE, f"{detail}; {note}" if note else detail)


def _side(ctx, commit, files, result, budget, tag) -> tuple[dict[str, str], str]:
    """(the outcomes of ``files`` on ``commit``, why they cannot be compared or "")."""
    (run,) = run_verifiers(ctx, commit, (VerifierSpec("p2p-probe", files),),
                           f"{result.attempt_id}-{tag}", timeout=budget)  # fmt: skip
    try:
        return _outcomes(run)
    finally:
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)  # not evidence for Core


def complete(run) -> bool:
    """#929: the run recorded, finished collecting, and gave every collected id an outcome (a base
    whose own config stops the session early leaves collected tests unrun, so unbound)."""
    return run.recorded and run.collection_finished and set(run.collected) <= set(run.outcomes)


def _outcomes(run) -> tuple[dict[str, str], str]:
    bad = (
        ("forged-bytecode", run.forged), ("timeout", run.timed_out), ("survivors", run.survivors),
        ("mutated-checkout", run.mutated), ("over-cap", run.report_too_big or run.log_too_big),
        ("teardown-error", run.teardown_errors), ("collection-error", run.collect_errors),
        ("collection-skip", run.collect_skipped), ("no-record", not run.recorded),
        ("collection-unfinished", not run.collection_finished), ("no-outcomes", not run.outcomes),
        ("exit-code", run.returncode not in (0, 1)),
        ("exit-1-without-failure", run.returncode == 1 and "failed" not in run.outcomes.values()),
        ("id-without-outcome", not set(run.collected) <= set(run.outcomes)),
    )  # fmt: skip
    return dict(run.outcomes), next((why for why, hit in bad if hit), "")
