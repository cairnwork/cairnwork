"""Entropy precheck for owner-declared blackbox answers."""

from __future__ import annotations

import json

from cairnwork.blackbox import Check, Declaration
from cairnwork.blackbox_values import guessable

DEGENERATE = {"true", "false", "null", "0", "1", "-1", '""', "[]", "{}"}


def _degenerate(item) -> bool:
    return guessable(item) or _key(item) in DEGENERATE


def _answers(check: Check) -> tuple[tuple[str, bool], ...]:
    """(answer key, degenerate) per case; a class case is degenerate when every call is."""
    if check.kind == "function":
        return tuple((_key(case), _degenerate(case)) for case in check.cases)
    return tuple(
        ("|".join(_key(call) for call in case.calls), all(_degenerate(call) for call in case.calls))
        for case in check.cases
    )


def _key(item) -> str:
    if item.raises:
        raised = json.loads(item.raises)
        return f"raises:{raised['type']}:{raised.get('message', '')}"
    if item.tolerance:
        return f"tol:{item.expected}"
    return item.expected


def declaration_problems(declaration: Declaration) -> tuple[str, ...]:
    """Return one fail-closed entropy problem for each check that is too guessable."""
    if not declaration.checks:
        return ("NO_CHECKS",)
    problems = []
    for check in declaration.checks:
        answers = _answers(check)
        safe = [key for key, degenerate in answers if not degenerate]
        if len(safe) >= 2 and len(set(safe)) >= 2:
            continue
        if not safe:
            problems.append(f"LOW_ENTROPY:{check.id}")
        else:
            problems.append(f"TOO_FEW_CASES:{check.id}")
    return tuple(problems)
