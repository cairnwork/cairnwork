"""Reviewable output: a JSON report, a markdown list, and an optional draft declaration file.

The declaration holds only protected obligations whose kinds this cairnwork version declares
(``KINDS_NOW``: top-level functions with JSON values without floats). It is written as the owner's
own file, mode 0600, never over an existing file, with a fresh random salt, and is checked with
``cairnwork.blackbox.parse`` so a draft the verifier would refuse is never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import KINDS_NOW, Obligation

MAX_CASES = 100
_UNSAFE = re.compile(r"[^a-z0-9_-]+")

CAUTIONS = (
    "Each draft is a suggestion read from the test's literals: review it before declaring it.",
    "The blackbox compares exact JSON types: a function returning 3.0, True or a tuple does not "
    "match 3, 1 or a list, although pytest's == would pass. Confirm the return type.",
    "Remove a declared case's answers from the deferred and visible tests the candidate's code "
    "can read (#311, and a candidate can copy the expected logic from them).",
    "Answers that are all true/false/null can be guessed (low-entropy): add cases that cannot.",
    "Replace a declaration as you replace deferred tests: every run reports which cases matched.",
)


def report(obligations: list[Obligation], kinds: Iterable[str]) -> dict[str, Any]:
    kinds = set(kinds)
    plain = [o.plain(kinds) for o in obligations]
    protected = sum(o["protected"] for o in plain)
    return {
        "tool": "blackbox_suggest",
        "version": 1,
        "kinds": sorted(kinds),
        "obligations": len(plain),
        "protected": protected,
        "coverage": protected / len(plain) if plain else None,
        "count_exact": all(o["exact"] for o in plain),
        "cautions": list(CAUTIONS),
        "items": plain,
    }


def markdown(data: dict[str, Any]) -> str:
    cov = data["coverage"]
    lines = [
        "# Blackbox declaration suggestions",
        "",
        f"Obligations: {data['obligations']}, protected with kinds {', '.join(data['kinds'])}: "
        f"{data['protected']}" + (f" ({cov:.1%})" if cov is not None else ""),
        "" if data["count_exact"] else "Some parametrize counts are not static: a lower bound.",
        "",
        *[f"- {c}" for c in data["cautions"]],
        "",
        "| Test | Where | Status | Draft or reason |",
        "|---|---|---|---|",
    ]
    for item in data["items"]:
        where = f"{item['file']}:{item['line']}"
        if item["protected"]:
            text = "; ".join(_case_text(c) for c in item["cases"])
            lines.append(f"| `{item['test_id']}` | {where} | draft | {_cell(text)} |")
        else:
            lines.append(f"| `{item['test_id']}` | {where} | refused | {_cell(item['reason'])} |")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _case_text(case: dict[str, Any]) -> str:
    target = case["target"]
    name = target.get("function") or f"{target['class']}(...).{target.get('method')}"
    call = (
        f"{target['module']}.{name}(args={json.dumps(case['args'])}, "
        f"kwargs={json.dumps(case['kwargs'])})"
    )
    expected = case["expected"]
    if "raises" in expected:
        return f"{call} raises {expected['raises']['module']}.{expected['raises']['name']}"
    return f"{call} -> {json.dumps(expected['value'])}"


def _id(text: str, taken: set[str], limit: int = 40) -> str:
    base = _UNSAFE.sub("-", text.lower()).strip("-")[: limit - 9] or "case"
    candidate = base
    if candidate in taken:
        digest = hashlib.sha256(f"{text}:{len(taken)}".encode()).hexdigest()[:8]
        candidate = f"{base}-{digest}"
    taken.add(candidate)
    return candidate


def declaration(obligations: list[Obligation]) -> dict[str, Any] | None:
    """The draft declaration (today's format) of the protected function-kind obligations."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in obligations:
        if not item.protected(KINDS_NOW):
            continue
        for case in item.cases:
            target = case["target"]
            groups.setdefault((target["module"], target["function"]), []).append(
                {
                    "id": item.test_id.rsplit("::", 1)[-1],
                    "args": case["args"],
                    "kwargs": case["kwargs"],
                    "expected": case["expected"]["value"],
                }
            )
    checks, check_ids = [], set()
    for (module, function), cases in sorted(groups.items()):
        for start in range(0, len(cases), MAX_CASES):
            case_ids: set[str] = set()
            chunk = [dict(c, id=_id(c["id"], case_ids)) for c in cases[start : start + MAX_CASES]]
            checks.append(
                {
                    "id": _id(function, check_ids),
                    "module": module,
                    "function": function,
                    "cases": chunk,
                }
            )
    if not checks:
        return None
    return {"salt": os.urandom(32).hex(), "checks": checks}


def write_declaration(path: Path, data: dict[str, Any]) -> None:
    """Validate with cairnwork's own parser, then write a new 0600 file (never overwrite)."""
    raw = json.dumps(data, indent=1, ensure_ascii=False).encode()
    from cairnwork.blackbox import parse  # the verifier's parser: a draft it refuses is a bug

    parse(raw)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw + b"\n")
