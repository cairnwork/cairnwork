"""#1196: a verifier sandbox that cannot read the process table is an environment limit.

The sweep that counts processes outliving a run reads the process table. When the sandbox
refuses that read, the count is unknown (``SWEEP_UNAVAILABLE``, never a pass). A run that is
unknown, or that failed with process residue and a tagged Seatbelt ``/bin`` read denial, is
inconclusive for the candidate: the same candidate is rerun when the table can be read. A
clean run, or a survivor without denial evidence, keeps its existing verdict.
"""

from __future__ import annotations

from cairnwork.deferred_tests import BaseUncomparable, Comparison


def table_unavailable(run) -> bool:
    if run.survivors < 0:
        return True
    if run.returncode in (None, 0) or run.survivors <= 0:
        return False
    return any(
        operation == "file-read-data" and target == "/bin" for operation, target in run.deny_denials
    )


def refuse_unreadable(runs):
    """The candidate's verifier runs; refuse as UNCOMPARABLE when the process table was unread."""
    if any(table_unavailable(one) for one in runs):
        raise BaseUncomparable(
            Comparison(
                "UNCOMPARABLE",
                "VERIFIER_PROCESS_TABLE_UNREADABLE",
                "the verifier sandbox could not read the process table; "
                "rerun the same candidate when available",
            )
        )
    return runs
