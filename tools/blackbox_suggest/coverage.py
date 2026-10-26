"""Protected acceptance coverage (#303, council 2026-10-27 必改項 4, Astra's definition).

Denominator: every required acceptance obligation, fixed before the candidate runs: each collected
test id (each parametrized case), including the ones that cannot be protected. Numerator: the
obligations with a protected (declarable) check. Two numbers are reported over tasks: the mean of
the per-task coverage (tasks weighted equally) and the share of tasks whose obligations are all
protected. A task with no obligations has no coverage to report and is counted separately.
"""

from __future__ import annotations

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


# Fixed labels, matched against the reasons the tool raises: a label never carries a name, a value
# or a line from the project, so aggregates can be published. Unknown reasons become "other".
CATEGORIES = (
    ("needs ", "needs a kind this version cannot declare"),
    ("autouse fixture", "autouse fixture in effect"),
    ("class autouse fixture", "autouse fixture in effect"),
    ("uses fixture", "uses fixtures"),
    ("setup hook", "setup or teardown hook"),
    ("hook ", "pytest hook in a conftest or the test module"),
    ("pytest_plugins", "pytest plugins loaded"),
    ("pytest config", "pytest configuration applies to every test"),
    ("pytest addopts", "pytest configuration applies to every test"),
    ("unreadable pytest configuration", "pytest configuration applies to every test"),
    ("module-level", "code runs at import time"),
    ("marked ", "marked (skip, xfail, filterwarnings, ...)"),
    ("this case carries marks", "marked (skip, xfail, filterwarnings, ...)"),
    ("decorated with", "other decorator"),
    ("async test", "async test"),
    ("parametrize", "parametrize not static or indirect"),
    ("the case's values", "parametrize not static or indirect"),
    ("indirect parametrization", "parametrize not static or indirect"),
    ("tuple", "tuple, set or bytes (not JSON)"),
    (" set", "tuple, set or bytes (not JSON)"),
    ("bytes", "tuple, set or bytes (not JSON)"),
    ("JSON has none", "tuple, set or bytes (not JSON)"),
    ("non-string dict key", "tuple, set or bytes (not JSON)"),
    ("not bound to a literal", "value is not a literal"),
    ("is not a literal", "value is not a literal"),
    ("f-string", "value is not a literal"),
    ("NaN", "value is not a literal"),
    ("*args", "value is not a literal"),
    ("unary sign", "value is not a literal"),
    ("unpacks a dict", "value is not a literal"),
    ("repeats the dict key", "value is not a literal"),
    ("approx(", "other approx shape"),
    ("**kwargs", "value is not a literal"),
    ("with-block", "other with-block"),
    (" is not pytest.raises", "other with-block"),
    ("raises() with match", "raises with match= or extra arguments"),
    ("raises", "other raises shape"),
    ("bound twice", "a name is rebound in the test"),
    ("list or dict is passed to several", "shared mutable argument"),
    ("never checked", "unchecked call (side effect)"),
    ("bare call", "unchecked call (side effect)"),
    ("no method of it", "unchecked call (side effect)"),
    ("is not a check", "other statement or control flow"),
    ("assignment to something", "other statement or control flow"),
    ("single comparison", "assert is not == / is"),
    ("assert uses", "assert is not == / is"),
    ("neither side of ==", "== does not compare a project call"),
    ("checked value is not a call", "== does not compare a project call"),
    ("not a module of the project", "callee outside the project"),
    ("not imported at the top", "callee is a builtin or local name"),
    ("rebound in the test file", "callee is defined or rebound in the test file"),
    ("attribute of", "callee is not a top-level function"),
    ("not a top-level name", "callee is not a top-level function"),
    ("plain name or dotted", "callee is not a top-level function"),
    ("method of a function's result", "callee is not a top-level function"),
    ("not a plain def or class", "callee is not a top-level function"),
    ("cannot find", "callee cannot be resolved statically"),
    ("re-exported through", "callee cannot be resolved statically"),
    ("not a class", "callee cannot be resolved statically"),
    ("cannot parse", "file cannot be parsed"),
    ("no assertion", "no assertion the tool can read"),
    ("nested too deeply", "nested too deeply"),
)


def category(reason: str) -> str:
    """A fixed label for the reason (never a name, value or line from the project)."""
    for needle, label in CATEGORIES:
        if needle in reason:
            return label
    return "other"
