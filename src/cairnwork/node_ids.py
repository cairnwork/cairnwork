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


def _split_top_level(text: str) -> list[str]:
    """Split on ``::`` outside ``[...]`` so a case id like ``test_y[x::y]`` stays whole."""
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]" and depth:
            depth -= 1
        elif depth == 0 and text.startswith("::", index):
            parts.append(text[start:index])
            start = index + 2
            index += 1
        index += 1
    parts.append(text[start:])
    return parts


def _locate(nodeid: str) -> tuple[str, list[str]] | None:
    """(file path, name parts) of a node id, or None when it has no ``::`` file boundary.

    The file boundary is the first ``.py`` followed by ``::`` (``dir.pyx/`` is not one), so
    ``::`` in a directory name is not mistaken for pytest's file/name separator; ``::`` inside
    a case id is not a separator."""
    boundary = next(
        (
            m.end()
            for m in re.finditer(r"\.py(?=::|$)", nodeid)
            if nodeid.count("[", 0, m.start()) == nodeid.count("]", 0, m.start())
        ),  # a ``.py::`` inside a case id has an open bracket before it
        None,
    )
    if boundary is not None:
        if boundary == len(nodeid):
            return None
        return nodeid[:boundary], _split_top_level(nodeid[boundary + 2 :])
    path, separator, suffix = nodeid.partition("::")
    return (path, _split_top_level(suffix)) if separator else None


def without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Remove pytest case suffixes from name parts while preserving bracketed paths."""
    located = _locate(nodeid)
    if located is None:
        return nodeid.split("[", 1)[0] if all_parts else nodeid
    path, parts = located
    for index in range(0 if all_parts else len(parts) - 1, len(parts)):
        parts[index] = parts[index].split("[", 1)[0]
    return path + "::" + "::".join(parts)


def _case_start(nodeid: str) -> int:
    """Index of the ``[`` that opens the last name part's case id, or -1."""
    located = _locate(nodeid)
    if located is None:
        return nodeid.find("[")
    last = located[1][-1]
    inside = last.find("[")
    return -1 if inside < 0 else len(nodeid) - len(last) + inside


def _normalise_id(nodeid: str, roots: tuple[str, ...]) -> str:
    start = _case_start(nodeid)
    if start < 0:
        return nodeid
    prefix, marker, params = nodeid[:start], "[", nodeid[start + 1 :]
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
    functions = {without_case(node) for node in present}
    for node in gone:
        function = without_case(node)
        start = _case_start(node)
        params = node[start + 1 :]
        if (
            start >= 0
            and function in functions
            and any(
                other[_case_start(other) + 1 :] != params
                for other in present
                if without_case(other) == function
            )
        ):
            return PER_RUN_NOTE
    return ""


def show(nodeid: str) -> str:
    """Return a bounded, printable node id with absolute paths masked."""
    return mask_paths(nodeid)
