"""The owner's verdict on a blackbox run (#303, blackbox.py): one Core observation per check.

The runner's output is data from a process the candidate shares, so it is parsed strictly and
never believed about pass or fail: the owner compares each case's outcome with the one only it
holds (blackbox_values.judge: exact JSON types, so ``True`` is not ``1`` and a list is not an
object; declared floats within their tolerance; a raise by type name and message). A check passes
only when the run was clean (sandboxed, as the verifier account, exit 0, no timeout, survivor,
checkout change or forged bytecode), the module came from the checkout, the output names exactly
the declared cases (and, for a class case, one outcome per declared call), and every outcome is the
expected one. Anything that kept the check from running or reading is
``VERIFICATION_INCOMPLETE: <code>``; a wrong value, a wrong or unexpected raise is ``MISMATCH``;
neither passes, and neither turns into another outcome such as UNCOMPARABLE.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cairnwork.blackbox import INCOMPLETE, Case, Check, Declaration
from cairnwork.blackbox_exec import RUNNER_SHA256, BlackboxRun, run_blackbox
from cairnwork.blackbox_values import BadOutput, error_of, judge
from cairnwork.core_request import VerifierObservation
from cairnwork.verifier_exec import _file_sha256, _record_digest


@dataclass(frozen=True)
class CheckVerdict:
    observation: VerifierObservation
    reason: str  # one line: "blackbox:<id>: PASS|MISMATCH|VERIFICATION_INCOMPLETE: <code> [...]"


def judge_blackbox(declaration: Declaration, repo, commit: str, run_root, run_id: str, **kwargs):
    """Run the declaration's checks on commit and judge them; never raises for a run that could not
    be made (that is VERIFICATION_INCOMPLETE). Returns (verdicts, log path or None)."""
    try:
        run = run_blackbox(declaration, repo, commit, run_root, run_id, **kwargs)
    except Exception as exc:  # noqa: BLE001 - declared but not run: incomplete, never a pass
        return _all_incomplete(declaration, commit, f"RUN_ERROR:{type(exc).__name__}"), None
    try:  # the output is the candidate's: whatever it makes the judging do, each check still fails
        return tuple(judge_run(declaration, commit, run)), run.log_path
    except Exception as exc:  # noqa: BLE001
        failed = f"JUDGE_ERROR:{type(exc).__name__}"
        return _all_incomplete(declaration, commit, failed), run.log_path


def _all_incomplete(declaration: Declaration, commit: str, failed: str) -> tuple[CheckVerdict, ...]:
    checks = declaration.checks
    return tuple(_verdict(check, commit, declaration, None, failed, {}) for check in checks)


def judge_run(declaration: Declaration, commit: str, run: BlackboxRun):
    problem = _run_problem(run)
    report = {} if problem else _report(run.report)
    if report is None:
        problem, report = "BAD_OUTPUT", {}
    for check in declaration.checks:
        outcomes: dict[str, str] = {}
        failed = problem or _check_problem(check, report, run, outcomes)
        yield _verdict(check, commit, declaration, run, failed, outcomes)


def _run_problem(run: BlackboxRun) -> str:
    for code, bad in (
        ("NO_SANDBOX", run.profile_sha256 is None),
        ("TIMEOUT", run.timed_out),
        ("SURVIVORS", run.survivors),
        ("CHECKOUT_CHANGED", run.mutated),
        ("FORGED_BYTECODE", run.forged),
        (f"EXIT_{run.returncode}", run.returncode != 0),
        ("NO_OUTPUT", run.report is None),
    ):
        if bad:
            return code
    return ""


def _report(raw: bytes | None) -> dict | None:
    try:
        data = json.loads(raw or b"", parse_float=_refuse, parse_constant=_refuse)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    checks = data.get("checks") if isinstance(data, dict) else None
    return checks if isinstance(checks, dict) else None


def _refuse(text: str):
    raise ValueError(text)


def _check_problem(check: Check, report: dict, run: BlackboxRun, outcomes: dict[str, str]) -> str:
    """Fill outcomes (case id -> match/mismatch/error:<type>); "" when the check could be read."""
    entry = report.get(check.id)
    if not isinstance(entry, dict) or not isinstance(entry.get("cases"), dict):
        error = entry.get("error") if isinstance(entry, dict) else None
        if not isinstance(error, str):
            return "BAD_OUTPUT"
        return "NOT_IMPORTED:" + "".join(c if c.isprintable() else "?" for c in error[:80])  # #319
    origin = entry.get("file")
    if not isinstance(origin, str) or not origin.startswith(run.checkout + "/"):
        return "NOT_FROM_CHECKOUT"
    cases = entry["cases"]
    if set(cases) != {case.id for case in check.cases}:
        return "BAD_OUTPUT"
    try:
        for case in check.cases:
            got = cases[case.id]
            outcomes[case.id] = judge(case, got) if check.kind == "function" else _calls(case, got)
    except BadOutput:
        return "BAD_OUTPUT"
    return ""


def _calls(case: Case, got) -> str:
    """A class case matches when every call's outcome does; a raising constructor is an error."""
    if isinstance(got, dict) and set(got) == {"init"}:
        return f"error:init:{error_of(got['init'])[0][:80]}"
    steps = got.get("steps") if isinstance(got, dict) and set(got) == {"steps"} else None
    if not isinstance(steps, list) or len(steps) != len(case.calls):
        raise BadOutput("steps")
    results = [judge(call, step) for call, step in zip(case.calls, steps, strict=True)]
    return "match" if all(result == "match" for result in results) else "mismatch"


def _verdict(check, commit, declaration, run, failed, outcomes) -> CheckVerdict:
    matched = sum(1 for value in outcomes.values() if value == "match")
    if failed:
        status = f"{INCOMPLETE}: {failed}"
    elif not run.account:  # the candidate ran as the owner and could read the answers (Q3)
        status = f"{INCOMPLETE}: BLACKBOX_NEEDS_VERIFIER_ACCOUNT"
    else:
        status = "PASS" if matched == len(check.cases) else "MISMATCH"
    low = ";low-entropy" if check.low_entropy else ""
    detail = f" [matched {matched}/{len(check.cases)}]" if outcomes else ""
    detail += " low-entropy" if low else ""
    digest = _record_digest(
        candidate_commit=commit, verifier_id=check.verifier_id,
        commitment=declaration.commitment(), runner_sha256=RUNNER_SHA256,
        sandbox_profile_sha256=run.profile_sha256 if run else None,
        returncode=run.returncode if run else None, timed_out=run.timed_out if run else False,
        survivors=run.survivors if run else 0, mutated=list(run.mutated) if run else [],
        forged_bytecode=list(run.forged) if run else [], account=run.account if run else False,
        output_sha256=hashlib.sha256(run.report).hexdigest() if run and run.report else None,
        log_sha256=_file_sha256(run.log_path) if run and run.log_path.exists() else None,
        outcomes=outcomes, status=status,
    )  # fmt: skip
    where = f"{run.log_path.parent.name}/blackbox.log" if run else "blackbox-not-run"
    observation = VerifierObservation(
        check.verifier_id, f"{where}#{check.id}{low}", f"sha256:{digest}", status == "PASS"
    )
    return CheckVerdict(observation, f"{check.verifier_id}: {status}{detail}")
