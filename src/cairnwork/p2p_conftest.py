"""#798 half 2: the differential base probe for a test commit that adds or changes a conftest.

A conftest can change which base tests run or pass, and its import-time effects reach the whole
test process. So the differential probe runs the way P2P really runs: the same set of files (the
mode's selection plus every base test file under a conftest's directory) in one process, once on
the BASE and once on the base with the WHOLE test commit laid over it. Every base test the test
commit does not change, add or remove must keep its outcome, else the test commit is refused
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

from cairnwork.conftest_check import conftest_of, governed
from cairnwork.deferred_tests import TestCommitRejected, deleted_of, lay_tests_over
from cairnwork.p2p_nodes import clean
from cairnwork.p2p_selection import name_rule, select_p2p_targets
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

CHANGES_BASE = "TEST_COMMIT_CONFTEST_CHANGES_BASE"
UNCHECKED = "TEST_COMMIT_CONFTEST_UNCHECKED"
# #891: with a conftest, a whole-suite probe that cannot run cannot narrow (see conftest_files)
GOVERNS = "; they include every base test file under a test-commit conftest's directory"


def conftest_files(task, test_files, mode, base_tests) -> tuple[str, ...]:
    """The files the P2P probe must run when the test commit has a conftest: every file the mode's
    selection runs plus every base test file under a conftest's directory, as one set (a
    conftest's import-time effects reach the whole test process). () without a conftest; refused
    UNCHECKED when P2P is off or there is no base test file at all.

    #891, decided: the fallback keeps these files. In whole-suite mode they already hold every
    base test (the mode's selection), wherever the conftest is; so when that probe cannot be used
    (timeout, cap), the module-dir fallback selects the same files and the task is UNCOMPARABLE
    (``P2P_BASE_PROBE_UNUSABLE``, detail ends with ``GOVERNS``): it fails closed. Dropping them
    from the fallback would leave the conftest's effect on the other base tests unchecked."""
    conftests = conftest_of(test_files)
    if not conftests:
        return ()
    if mode == "off":
        raise TestCommitRejected(
            UNCHECKED, "p2p_selection is off: nothing would check the conftest"
        )
    existing = base_tests(task, frozenset(deleted_of(test_files)))
    rule = name_rule(task.repo, task.base_commit)  # #870: the same selection P2P makes
    chosen = select_p2p_targets(task.allowed_files, existing, task.test_roots, mode, rule)
    forced = tuple(sorted({*chosen, *governed(conftests, existing)}))
    if not forced:
        raise TestCommitRejected(UNCHECKED, f"{', '.join(conftests)}: no base test file to compare")
    return forced


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
    after, why = _side(ctx, over, files, result, budget, "co")
    if why:
        raise TestCommitRejected(UNCHECKED, f"the probe with the test commit is unusable: {why}")
    skip = side.changed | side.probe_only
    kept = {n: o for n, o in outcomes.items() if n.split("[", 1)[0] not in skip}
    differ = sorted(n for n, o in kept.items() if after.get(n) != o)
    if differ:
        shown = ", ".join(map(clean, differ[:5])) + (" ..." if len(differ) > 5 else "")
        raise TestCommitRejected(CHANGES_BASE, f"{len(differ)} base test(s) differ: {shown}")


def _side(ctx, commit, files, result, budget, tag) -> tuple[dict[str, str], str]:
    """(the outcomes of ``files`` on ``commit``, why they cannot be compared or "")."""
    (run,) = run_verifiers(ctx, commit, (VerifierSpec("p2p-probe", files),),
                           f"{result.attempt_id}-{tag}", timeout=budget)  # fmt: skip
    try:
        return _outcomes(run)
    finally:
        shutil.rmtree(Path(run.log_path).parent, ignore_errors=True)  # not evidence for Core


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
