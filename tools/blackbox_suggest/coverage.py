"""Protected acceptance coverage (#303, council 2026-10-27 必改項 4, Astra's definition).

Denominator: every required acceptance obligation, fixed before the candidate runs: each collected
test id (each parametrized case), including the ones that cannot be protected. Numerator: the
obligations with a protected (declarable) check. Two numbers are reported over tasks: the mean of
the per-task coverage (tasks weighted equally) and the share of tasks whose obligations are all
protected. A task with no obligations has no coverage to report and is counted separately.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from . import Obligation


def task_coverage(obligations: list[Obligation], kinds: Iterable[str]) -> dict[str, Any]:
    kinds = set(kinds)
    total = len(obligations)
    protected = sum(o.protected(kinds) for o in obligations)
    reasons = Counter(
        category(o.reason(kinds) or "") for o in obligations if not o.protected(kinds)
    )
    return {
        "obligations": total,
        "protected": protected,
        "coverage": protected / total if total else None,
        "exact": all(o.exact for o in obligations),
        "reasons": dict(reasons),
    }


def aggregate(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """The two reported numbers over per-task results from task_coverage."""
    measured = [t for t in tasks if t["obligations"]]
    reasons: Counter[str] = Counter()
    for t in tasks:
        reasons.update(t["reasons"])
    n = len(measured)
    return {
        "tasks": len(tasks),
        "tasks_without_obligations": len(tasks) - n,
        "mean_task_coverage": sum(t["coverage"] for t in measured) / n if n else None,
        "fully_protected_share": sum(t["protected"] == t["obligations"] for t in measured) / n
        if n
        else None,
        "obligations": sum(t["obligations"] for t in measured),
        "protected": sum(t["protected"] for t in measured),
        "tasks_count_exact": sum(t["exact"] for t in measured),
        "unprotected_reasons": dict(reasons.most_common()),
    }


_LINE = re.compile(r"^line \d+: ")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_AFTER = re.compile(
    r"^(uses fixture\(s\)|decorated with|autouse fixture|class setup hook|"
    r"module setup hook|class autouse fixture|a bare call|marked)(?=\W|$).*"
)
_WITH = re.compile(r"^with [A-Za-z_][\w.]*\(")


def category(reason: str) -> str:
    """The reason without names, line numbers or values: safe to aggregate and publish."""
    text = _LINE.sub("", reason)
    text = _AFTER.sub(lambda m: m.group(1) + " …", text)
    text = _WITH.sub("with …(", text)
    text = _QUOTED.sub("…", text)
    text = _DOTTED.sub("…", text)
    text = re.sub(r"\b(in|of) [A-Za-z_]\w* by\b", r"\1 … by", text)
    return re.sub(r"^[A-Za-z_]\w* is ", "… is ", text)
