"""Salt-independent single-use protection for a candidate and check set."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from cairnwork.blackbox import Declaration
from cairnwork.verify_context import VerifyContext

from .judge import ProtectedResult
from .once import OnceStore
from .policy import declaration_problems
from .run import run_protected

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def attempt_key(declaration: Declaration, commit: str) -> str:
    """Hash the canonical checks and candidate commit, deliberately excluding the salt."""
    checks = sorted(
        (
            check | {"cases": sorted(check["cases"], key=lambda case: case["id"])}
            for check in declaration._plain(with_expected=True)["checks"]
        ),
        key=lambda check: check["id"],
    )
    payload = json.dumps(
        {"checks": checks, "commit": commit},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_protected_once(
    declaration: Declaration,
    commit: str,
    *,
    store: OnceStore,
    attempts_store: OnceStore,
    ctx: VerifyContext,
    run_id: str,
    **kw: Any,
) -> ProtectedResult:
    """Consume a salt-independent attempt before delegating to the normal protected run."""
    if not _COMMIT.fullmatch(commit):
        return ProtectedResult("REFUSED", "BAD_COMMIT", ())
    problems = declaration_problems(declaration)
    if problems:
        return ProtectedResult("REFUSED", problems[0], ())
    if ctx.account is None:
        return ProtectedResult("REFUSED", "NEEDS_VERIFIER_ACCOUNT", ())
    if not attempts_store.consume(attempt_key(declaration, commit), commit):
        return ProtectedResult("REFUSED", "ATTEMPT_REUSED", ())
    return run_protected(declaration, ctx, commit, run_id, store, **kw)
