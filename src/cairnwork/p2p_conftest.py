"""#798 half 2: the differential base probe for a test commit that adds or changes a conftest.

A conftest can change which base tests run or pass. Instead of listing what a conftest may not do,
the test commit's conftest file(s) alone are laid over the BASE tree and the same probe (same
files, pinned ini, sandbox) as the plain base probe is run on both trees. Every node id must have
the same outcome and the id set must be the same, else the test commit is refused
(``TEST_COMMIT_CONFTEST_CHANGES_BASE``: the test author's error, not the candidate's).

The comparison is only made when it can be trusted, else ``TEST_COMMIT_CONFTEST_UNCHECKED``, never
"equal": P2P is on (not ``off``); the probe covers every base test file under each conftest's
directory (forced coverage, independent of the mode's selection), within the module-dir cap and
the time budget; on both sides the run was clean, collection finished with no collection error or
skip, and every collected id has an outcome.

The claim is narrow and stated: base outcomes are unchanged by the conftest. Behaviour that
depends on the candidate's code is trusted test-author code, as any test file is. A receiver can
re-run the static check (conftest_check.py) but not this probe: that part is the signer's word.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cairnwork.conftest_check import conftest_of, governed
from cairnwork.deferred_tests import TestCommitRejected, deleted_of, lay_tests_over
from cairnwork.p2p_selection import MODULE_DIR_CAP
from cairnwork.verifier_spec import VerifierSpec
from cairnwork.verifiers import run_verifiers

CHANGES_BASE = "TEST_COMMIT_CONFTEST_CHANGES_BASE"
UNCHECKED = "TEST_COMMIT_CONFTEST_UNCHECKED"


def check_conftest(task, test_files, mode, result, ctx, base_tests, budget) -> tuple[str, ...]:
    """The base test files the conftest governs (the probe must run them), once the differential
    probe finds their outcomes unchanged; refuses otherwise. ``base_tests`` lists the existing
    base test files (p2p._base_tests). ()  when the test commit has no conftest."""
    conftests = conftest_of(test_files)
    if not conftests:
        return ()
    if mode == "off":
        raise TestCommitRejected(
            UNCHECKED, "p2p_selection is off: nothing would check the conftest"
        )
    forced = governed(conftests, base_tests(task, frozenset(deleted_of(test_files))))
    if not forced or len(forced) > MODULE_DIR_CAP:
        why = (
            "no base test file under it"
            if not forced
            else f"{len(forced)} files, cap {MODULE_DIR_CAP}"
        )
        raise TestCommitRejected(UNCHECKED, f"{', '.join(conftests)}: {why}")
    over = lay_tests_over(task.repo, task.base_commit, task.test_commit, conftests,
                          f"{result.attempt_id}-cf", Path(ctx.run_root))  # fmt: skip
    sides = {}
    for name, commit in (("base", task.base_commit), ("with the conftest", over)):
        sides[name], why = _side(ctx, commit, forced, result, budget, name[:2])
        if why:
            raise TestCommitRejected(UNCHECKED, f"the probe {name} is unusable: {why}")
    base_side, over_side = sides.values()
    if base_side != over_side:
        differ = sorted(n for n in {*base_side, *over_side} if base_side.get(n) != over_side.get(n))
        shown = ", ".join(differ[:5]) + (" ..." if len(differ) > 5 else "")
        raise TestCommitRejected(CHANGES_BASE, f"{len(differ)} base test(s) differ: {shown}")
    return forced


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
