"""Owner-side verdict wrapper; this result is diagnostic and never VERIFIED."""

from __future__ import annotations

from dataclasses import dataclass

from cairnwork.blackbox import Declaration
from cairnwork.blackbox_exec import BlackboxRun
from cairnwork.blackbox_judge import judge_run

from .policy import declaration_problems


@dataclass(frozen=True)
class ProtectedResult:
    status: str
    code: str
    checks: tuple[str, ...]

    def worker_view(self) -> str:
        return self.status


def _incomplete(code: str) -> ProtectedResult:
    return ProtectedResult("INCOMPLETE", code, ())


def _account_problem(run: BlackboxRun) -> ProtectedResult | None:
    return ProtectedResult("REFUSED", "NEEDS_VERIFIER_ACCOUNT", ()) if not run.account else None


def judge(declaration: Declaration, commit: str, run: BlackboxRun | None) -> ProtectedResult:
    problems = declaration_problems(declaration)
    if problems:
        return ProtectedResult("REFUSED", problems[0], ())
    if run is None:
        return _incomplete("NOT_RUN")
    refusal = _account_problem(run)
    if refusal:
        return refusal
    if run.profile_sha256 is None:
        return _incomplete("NO_SANDBOX")
    if run.timed_out:
        return _incomplete("TIMEOUT")
    if run.returncode is None:
        return _incomplete("NO_EXIT")
    if run.returncode < 0:
        return _incomplete("KILLED")
    for code, found in (
        ("SURVIVORS", run.survivors),
        ("CHECKOUT_CHANGED", run.mutated),
        ("FORGED_BYTECODE", run.forged),
    ):
        if found:
            return _incomplete(code)
    try:
        verdicts = tuple(judge_run(declaration, commit, run))
    except Exception:  # noqa: BLE001 - oracle failure can never produce a pass
        return _incomplete("JUDGE_ERROR")
    expected = len(declaration.checks)
    lines = tuple(verdict.reason for verdict in verdicts)
    if len(verdicts) == expected and all(verdict.observation.passed for verdict in verdicts):
        return ProtectedResult("PROTECTED_PASS", "OK", lines)
    for line in lines:
        marker = "VERIFICATION_INCOMPLETE: "
        if marker in line:
            return _incomplete(line.split(marker, 1)[1].split()[0])
    failed = next(
        (
            check.id
            for check in declaration.checks
            if not any(line.startswith(f"{check.verifier_id}: PASS") for line in lines)
        ),
        "unknown",
    )
    return ProtectedResult("MISMATCH", f"MISMATCH:{failed}", lines)
