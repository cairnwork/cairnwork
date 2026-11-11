"""Normalise per-checkout pytest node ids and redact paths in refusal text."""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from cairnwork.verdict_record import Verdict

PLACEHOLDER = "<cw-checkout>"
PER_RUN_NOTE = (
    "if a parametrised test id differs between runs (per-run value such as a time, a random value, "
    "or a path outside the checkout); give the parameters stable ids "
    "(pytest.param(..., id=...) or ids=) so the base and the candidate id match"
)
_PATH = re.compile(r"(?<![A-Za-z0-9._/-])/(?:[^\s/'\"<>\]]+/)+[^\s/'\"<>\]]*")
_MAP_FIELDS = ("outcomes", "paths", "fail_kind", "denied", "setup_denied")
_TUPLE_FIELDS = ("collect_errors", "collect_skipped", "collected", "renamed", "teardown_errors")


def _ids(verdict: Verdict) -> set[str]:
    found = {node for field in _MAP_FIELDS for node in getattr(verdict, field)}
    found.update(node for field in _TUPLE_FIELDS for node in getattr(verdict, field))
    return found


def _normalise_id(nodeid: str, roots: tuple[str, ...]) -> str:
    prefix, marker, params = nodeid.partition("[")
    if not marker:
        return nodeid
    patterns = [
        re.compile(rf"(?<![A-Za-z0-9._/-]){re.escape(root)}(?=$|/|[^A-Za-z0-9._/-])")
        for root in roots
    ]
    if PLACEHOLDER not in params and not any(pattern.search(params) for pattern in patterns):
        return nodeid  # ids without the checkout or the placeholder are left byte-identical
    # escape a literal placeholder first, so it can never equal a substituted checkout root
    params = params.replace("\\", "\\\\").replace(PLACEHOLDER, "\\" + PLACEHOLDER)
    for pattern in patterns:
        params = pattern.sub(PLACEHOLDER, params)
    return prefix + marker + params


def normalise_run_ids(verdict: Any, checkout_root: str | Path) -> Any:
    """Replace this checkout's path while preserving distinct literal parameter values."""
    raw_ids = _ids(verdict)
    checkout = str(checkout_root)
    roots = tuple(sorted({checkout, os.path.realpath(checkout)}, key=len, reverse=True))
    converted = {node: _normalise_id(node, roots) for node in raw_ids}
    if len(set(converted.values())) != len(converted):
        return verdict

    updates = {}
    for field in _MAP_FIELDS:
        value = getattr(verdict, field)
        updates[field] = {converted[node]: item for node, item in value.items()}
    for field in _TUPLE_FIELDS:
        updates[field] = tuple(converted[node] for node in getattr(verdict, field))
    return replace(verdict, **updates)


def mask_paths(text: str) -> str:
    """Make a refusal detail printable, bounded and free of absolute POSIX paths."""
    clean = "".join(char if char.isprintable() else "?" for char in str(text))
    return _PATH.sub("<path>", clean)[:200]


def unmatched_note(gone, present) -> str:
    """Explain missing parameter variants only when their test function still exists."""
    functions = {node.split("[", 1)[0] for node in present}
    for node in gone:
        function, marker, params = node.partition("[")
        if (
            marker
            and function in functions
            and any(
                other.partition("[")[2] != params
                for other in present
                if other.startswith(function + "[")
            )
        ):
            return PER_RUN_NOTE
    return ""


def show(nodeid: str) -> str:
    """Return a bounded, printable node id with absolute paths masked."""
    return mask_paths(nodeid)
