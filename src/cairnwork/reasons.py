"""The reason text of a judged attempt, and why a verifier run did not pass (Issue #114).

A run passes only if every check holds (verifiers._run_one); Core then sees a bare FAIL and
answers FAILED_VERIFICATION, which says nothing about a run whose log shows passing tests.
The reasons are recorded next to the verifier id, so a failure is always explainable.
"""

from __future__ import annotations

import os
import re
import unicodedata

from cairnwork import survivor_argv
from cairnwork.textclean import replace_hidden

MAX_NAMES = 5
MAX_NAME_CHARS = 120
# The record delimiters of explain(); control and invisible characters go by category.
_DELIMITERS = re.compile(r"[\[\];:]")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
MAX_ASSERTION_CHARS = 240


def _assertion_lines(assertions, failed) -> list[str]:
    """Render bounded, single-line assertion text without terminal or workflow controls."""
    shown = []
    for node in failed[:MAX_NAMES]:
        text = _ANSI.sub("", assertions.get(node, "")).splitlines()
        summary = next(
            (line.strip() for line in text if line.strip()), "assertion detail unavailable"
        )
        summary = "".join("?" if unicodedata.category(ch).startswith("C") else ch for ch in summary)
        if summary.startswith(("::", "##[")):
            summary = "?" + summary
        if summary:
            safe_node = _safe_line(node)
            shown.append(f"{safe_node}: {summary[:MAX_ASSERTION_CHARS]}")
    return shown


def _safe_line(text: str) -> str:
    clean = "".join("?" if unicodedata.category(ch).startswith("C") else ch for ch in text)
    clean = replace_hidden(clean)
    return "?" + clean if clean.startswith(("::", "##[")) else clean


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
        "env": "environment error (OSError, MemoryError, RecursionError, child) in",
        "plugin": "missing pytest plugin fixture in",
    }
    return [f"{said[kind]} {_names([node])[3:]}" for node, kind in fail_kind.items()][:MAX_NAMES]


def _outcome_cause(outcomes) -> str:
    """Summarize collected tests that did not pass; outcome values are untrusted text."""
    failed = [(node, result) for node, result in outcomes.items() if result != "passed"]
    if not failed:
        return ""
    counts = {}
    for _node, result in failed:
        counts[result] = counts.get(result, 0) + 1
    kinds = sorted(counts.items())
    shown = [
        f"{count} {_DELIMITERS.sub('?', replace_hidden(str(kind)))[:MAX_NAME_CHARS]}"
        for kind, count in kinds[:MAX_NAMES]
    ]
    more = len(kinds) - len(shown)
    summary = ", ".join(shown) + (f", +{more} more" if more > 0 else "")
    names = _names(node for node, _result in failed)
    return f"pytest collected tests that did not pass ({summary}: {names.split(': ', 1)[1]})"


def candidate_denial(outcomes, denied, write_roots) -> str:
    """#962: the denied path when EVERY test that did not pass in a candidate run failed with an
    EPERM denial (report_guard's ``denied``, kind ``env``) at a path outside ``write_roots``; else
    ``""``. Wording only: the verdict stays FAILED. A plain failure, a skip or any other outcome in
    the mix keeps today's reason; the caller passes nothing unless the run was otherwise clean."""
    failed = [node for node, result in outcomes.items() if result != "passed"]
    roots = [os.path.normpath(str(root)) for root in write_roots]
    paths = [denied.get(node, "") if outcomes[node] == "failed" else "" for node in failed]
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
    renamed=(), exitstatus=None, testsfailed=None, fail_kind=None, data_refused=(),
    denied_path="",
    all_skipped: bool = False,
    outcomes=None,
    assertions=None,
    hidden_tests: bool = False,
) -> str:  # fmt: skip
    """The causes that made a run fail, ``""`` if none (then the run passed)."""
    hidden_failures = [node for node, outcome in (outcomes or {}).items() if outcome == "failed"]
    if hidden_tests and hidden_failures:
        return f"deferred: {len(hidden_failures)} failed"
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
    if data_refused:  # #850 data_exec_hook.py: refused even when the test caught the refusal
        causes.append(f"DATA_EXEC_REFUSED: declared test data executed ({_names(data_refused)})")
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
        failures = [node for node, outcome in (outcomes or {}).items() if outcome == "failed"]
        causes.extend(_kind_causes(fail_kind or {}))
        if failures:
            details = _assertion_lines(assertions or {}, failures)
            if len(failures) > MAX_NAMES:
                details.append(f"+{len(failures) - MAX_NAMES} more")
            causes.append("assertions: " + "; ".join(details))
        if denied_path:  # #962: a candidate run; base denial is BASE_ENV_ERROR
            causes.append(
                f"the failing tests report an operation not permitted (EPERM) at"
                f" {_names([denied_path])[3:]}, outside the run's write roots: unless the change"
                " raised this itself, likely the run environment, not evidence about the change"
            )
    elif ran_sandboxed and returncode == 0 and not tests_ran and not_run and not all_skipped:
        causes.append(
            f"pytest passed but no test ran for a target or skipped module ({_names(not_run)})"
        )
    elif ran_sandboxed and returncode == 0 and not tests_ran and not renamed and not disagrees:
        cause = _outcome_cause(outcomes or {})
        causes.append(
            cause
            or (
                "pytest passed but every collected test was skipped"
                if all_skipped
                else "pytest passed but the guard saw no test run"
            )
        )
    return "; ".join(causes)


def verifier_failure_reason(
    passed,
    sandboxed,
    returncode,
    timed_out,
    mutated,
    survivors,
    log_too_big,
    tests_ran,
    verdict,
    forged,
    not_run,
    denied_path,
    hidden_tests,
):
    """Render one verifier's failure using its bounded guard record."""
    if passed:
        return ""
    return why_not_passed(
        ran_sandboxed=sandboxed, returncode=returncode, timed_out=timed_out,
        mutated=mutated, survivors=survivors, log_too_big=log_too_big,
        tests_ran=tests_ran, report_too_big=verdict.too_big, forged=forged,
        not_run=not_run, renamed=verdict.renamed, exitstatus=verdict.exitstatus,
        testsfailed=verdict.testsfailed, fail_kind=verdict.fail_kind,
        data_refused=verdict.data_refused, denied_path=denied_path,
        all_skipped=all_skipped(verdict.collected, verdict.outcomes), outcomes=verdict.outcomes,
        assertions=verdict.assertions, hidden_tests=hidden_tests,
    )  # fmt: skip


def all_skipped(collected, outcomes) -> bool:
    """Whether a complete, nonempty collection has only skipped outcomes."""
    return (
        bool(collected)
        and set(outcomes) == set(collected)
        and all(outcome == "skipped" for outcome in outcomes.values())
    )


def plain_test_failure(sandboxed: bool, returncode, clean: bool, outcomes) -> bool:
    """Whether a clean sandboxed pytest run reports a plain assertion failure."""
    return sandboxed and returncode == 1 and clean and "failed" in outcomes.values()


def _missing_modules(run) -> str:
    """#796 S3: the new submodules a failed run's test files could not import (the guard's
    re-validated record), so a candidate that does not add the module is told so; text only."""
    modules = [module for _node, module, _origin in getattr(run, "submodule_errors", ())]
    return f"; collection could not import {_names(modules)}" if modules else ""


def skip_origins(run, base) -> str:
    """#1565: where each skipped test of a failed run comes from, by the base run's record (text
    only, the verdict stays FAILED): ``base-skip`` (skipped on the base too), ``candidate-skip``
    (the base ran it), ``new-skip`` (no such node on the base), ``always-skip`` (all skipped).
    ``""`` without that base record or when the base's skip list hit its cap (then unknown)."""
    vid = run.spec.verifier_id
    nodes = getattr(base, "base_nodes", {}).get(vid)
    skips = getattr(base, "base_skips", {}).get(vid)
    own = getattr(run, "skips", None)  # this run's own skip record; an xfail is no skip there
    if not nodes or skips is None or own is None or len(skips) >= 50 or len(own) >= 50:
        return ""
    skipped = sorted(n for n, result in run.outcomes.items() if result == "skipped" and n in own)
    if not skipped:
        return ""
    if len(skipped) == len(run.outcomes) == len(nodes) and set(skipped) <= set(skips) <= set(nodes):
        return " (skip origin: always-skip)"
    groups = {
        "base-skip": [n for n in skipped if n in skips],
        "candidate-skip": [n for n in skipped if n in nodes and n not in skips],
        "new-skip": [n for n in skipped if n not in nodes],
    }
    shown = "; ".join(f"{kind} {_names(found)}" for kind, found in groups.items() if found)
    return f" (skip origin: {shown})" if shown else ""


def explain(runs, base=None) -> str:
    """``[v1: cause; v2: cause]`` for the runs that failed, then ``[notes v3: note, note]`` for the
    backend notes of the runs that passed (Issue #584); ``""`` when every run passed without
    notes. Notes never change the verdict: they only say what the host left open around a run."""
    failed = [
        f"{run.spec.verifier_id}: {run.why or 'no cause recorded'}"
        + _missing_modules(run)
        + (skip_origins(run, base) if "collected tests that did not pass" in run.why else "")
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
# import <name>`` (or, #796 S3, ``import <in-repo package>.<new module>``) that fails on the base
# is accepted as teeth; anything else must move inside.
COLLECT_HINT = (
    "; import fix-introduced symbols inside the test function (a module-level"
    " 'from <existing in-repo module> import <name>' or 'import <existing in-repo package>.<new"
    " module>' is also accepted)"
)
