"""The reason text of a judged attempt, and why a verifier run did not pass (Issue #114).

A run passes only if every check holds (verifiers._run_one); Core then sees a bare FAIL and
answers FAILED_VERIFICATION, which says nothing about a run whose log shows passing tests.
The reasons are recorded next to the verifier id, so a failure is always explainable.
"""

from __future__ import annotations

import os
import re

from cairnwork import survivor_argv
from cairnwork.textclean import replace_hidden

MAX_NAMES = 5
MAX_NAME_CHARS = 120
# The record delimiters of explain(); control and invisible characters go by category.
_DELIMITERS = re.compile(r"[\[\];:]")


def _names(names) -> str:
    """A count and at most MAX_NAMES names, each cut and stripped of control characters: the
    names are written by the code under test and end up in a recorded reason."""
    items = list(names)
    shown = [
        _DELIMITERS.sub("?", replace_hidden(str(n)))[:MAX_NAME_CHARS] for n in items[:MAX_NAMES]
    ]
    more = len(items) - len(shown)
    return f"{len(items)}: {', '.join(shown)}" + (f", +{more} more" if more > 0 else "")


def _kind_causes(fail_kind) -> list[str]:
    """#775: a test that failed in fixture setup or with an OSError (report_guard's
    ``fail_kind``). Wording only: the verdict stays FAILED and nothing parses it."""
    said = {
        "setup": "setup error in",
        "env": "environment error (OSError) in",
        "plugin": "missing pytest plugin fixture in",
    }
    return [f"{said[kind]} {_names([node])[3:]}" for node, kind in fail_kind.items()][:MAX_NAMES]


def candidate_denial(outcomes, denied, write_roots) -> str:
    """#962: the denied path when EVERY failed test of a candidate run died with a sandbox-denied
    write (report_guard's ``denied``, kind ``env``) to a path outside ``write_roots``; else ``""``.
    Wording only: the verdict stays FAILED. A plain failure or a mix keeps today's reason."""
    failed = [node for node, result in outcomes.items() if result == "failed"]
    roots = [os.path.normpath(str(root)) for root in write_roots]
    paths = [denied.get(node, "") for node in failed]
    if not failed or not all(
        p.startswith("/") and not any(_within(p, r) for r in roots) for p in paths
    ):
        return ""
    return paths[0]


def _within(path: str, root: str) -> bool:
    path = os.path.normpath(path)
    return path == root or path.startswith(root.rstrip("/") + "/")


def verdict_reason(verified: bool, verdict, recorded) -> str:
    """The reason string for a judged attempt. A verified attempt reads ``CORE_<status>``. Otherwise
    the readback's reason is shown (``recorded`` when present, so an unverified readback never reads
    as CORE_VERIFIED; ``verdict`` only if there is no readback); when that reason is empty, a Core
    that said VERIFIED but did not verify on readback reads ``CORE_NOT_VERIFIED``, else
    ``CORE_<status>``."""
    if verified:
        return f"CORE_{verdict.status}"
    shown = recorded or verdict
    if shown.reason:
        return shown.reason
    return "CORE_NOT_VERIFIED" if verdict.status == "VERIFIED" else f"CORE_{verdict.status}"


def why_not_passed(
    *, ran_sandboxed: bool, returncode, timed_out: bool, mutated, survivors: int,
    log_too_big: bool, tests_ran: bool, report_too_big: bool = False, forged=(), not_run=(),
    renamed=(), exitstatus=None, testsfailed=None, fail_kind=None,
    denied_path="",
) -> str:  # fmt: skip
    """The causes that made a run fail, ``""`` if none (then the run passed)."""
    causes = []
    if not ran_sandboxed:
        causes.append("no sandbox backend: verifier not executed")
    if timed_out:
        causes.append("timed out")
    if mutated:
        causes.append(f"checkout changed ({_names(mutated)})")
    if forged:  # pycache_guard.py, bytecode_roots.py: bytecode the run may not supply
        causes.append(
            f"FORGED_BYTECODE: bytecode cache holds code the run may not supply ({_names(forged)})"
        )
    if survivors:
        causes.append(
            f"{survivors} process(es) outlived the run, or the process list was unreadable"
            + survivor_argv.listing(survivors)  # #814: allow-listed argv prefixes, text only
        )
    if log_too_big:
        causes.append("log over the size cap")
    if report_too_big:
        causes.append("test report over the size cap")
    if renamed:  # report_guard.py: a test's node id or file changed after pytest collected it
        causes.append(f"items renamed after collection ({_names(renamed)})")
    # report_guard.py: pytest's own status as recorded at sessionfinish (None: no record)
    disagrees = (
        ran_sandboxed and not timed_out and exitstatus is not None
        and (exitstatus != returncode or (returncode == 0 and testsfailed != 0))
    )  # fmt: skip
    if disagrees:
        causes.append(
            f"process exit status {returncode} disagrees with the recorded session status "
            f"(exit status {exitstatus}, {testsfailed} failed)"
        )
    if ran_sandboxed and not timed_out and returncode != 0:
        causes.append(f"pytest exit code {returncode}")
        causes.extend(_kind_causes(fail_kind or {}))
        if denied_path:  # #962: a candidate run; the same denial on a base run is BASE_ENV_ERROR
            causes.append(
                f"the failing tests report a write denied at {_names([denied_path])[3:]}, outside"
                " the run's write roots: unless the change raised this itself, likely the run"
                " environment, not evidence about the change"
            )
    elif ran_sandboxed and returncode == 0 and not tests_ran and not_run:
        causes.append(
            f"pytest passed but no test ran for a target or skipped module ({_names(not_run)})"
        )
    elif ran_sandboxed and returncode == 0 and not tests_ran and not renamed and not disagrees:
        causes.append("pytest passed but the guard saw no test run")
    return "; ".join(causes)


def explain(runs) -> str:
    """``[v1: cause; v2: cause]`` for the runs that failed, then ``[notes v3: note, note]`` for the
    backend notes of the runs that passed (Issue #584); ``""`` when every run passed without
    notes. Notes never change the verdict: they only say what the host left open around a run."""
    failed = [
        f"{run.spec.verifier_id}: {run.why or 'no cause recorded'}"
        + "".join(f" ({note})" for note in run.notes)  # fixed strings from the backend
        for run in runs
        if not run.observation.passed
    ]
    noted = [
        f"{run.spec.verifier_id}: {', '.join(run.notes)}"  # fixed strings from the backend
        for run in runs
        if run.observation.passed and run.notes
    ]
    return (f" [{'; '.join(failed)}]" if failed else "") + (
        f" [notes {'; '.join(noted)}]" if noted else ""
    )


def skip_hint(run) -> str:
    """#813: the module a collector skipped for (``importorskip``); text for the reason, the
    verdict is not read from it. The names come from the run's record: cut and cleaned here."""
    deps = getattr(run, "skipped_deps", ())
    return f"; skipped at collection, could not import {_names(deps)}" if deps else ""


# #796: the hint for a base collect error in a target file. A module-level ``from <in-repo module>
# import <name>`` that fails on the base is accepted as teeth; anything else must move inside.
COLLECT_HINT = (
    "; import fix-introduced symbols inside the test function (a module-level"
    " 'from <existing in-repo module> import <name>' is also accepted)"
)
