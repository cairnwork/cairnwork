"""End-to-end owner-side protected run; not wired into Cairnwork core."""

from __future__ import annotations

import re

from cairnwork.blackbox import Declaration
from cairnwork.blackbox_exec import run_blackbox
from cairnwork.verify_context import VerifyContext

from .judge import ProtectedResult, judge
from .once import OnceStore
from .policy import declaration_problems

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _run_problem(ctx: VerifyContext) -> ProtectedResult | None:
    if ctx.account is None:
        return ProtectedResult("REFUSED", "NEEDS_VERIFIER_ACCOUNT", ())
    return None


def run_protected(
    declaration: Declaration,
    ctx: VerifyContext,
    commit: str,
    run_id: str,
    store: OnceStore,
    *,
    timeout: int | None = None,
) -> ProtectedResult:
    if not _COMMIT.fullmatch(commit):
        return ProtectedResult("REFUSED", "BAD_COMMIT", ())
    problems = declaration_problems(declaration)
    if problems:
        return ProtectedResult("REFUSED", problems[0], ())
    refusal = _run_problem(ctx)
    if refusal:
        return refusal
    commitment = declaration.commitment().removeprefix("sha256:")
    if not store.consume(commitment, commit):
        return ProtectedResult("REFUSED", "DECLARATION_REUSED", ())
    try:
        kwargs = {"timeout": timeout} if timeout is not None else {}
        run = run_blackbox(declaration, ctx, commit, run_id, **kwargs)
    except Exception as exc:  # noqa: BLE001 - consumed attempt remains used on all run failures
        return ProtectedResult("INCOMPLETE", f"RUN_ERROR:{type(exc).__name__}", ())
    return judge(declaration, commit, run)
