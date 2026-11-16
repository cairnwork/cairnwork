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


_NAME = re.compile(r"[^\W\d]\w*(::[^\W\d]\w*)*(\[.*\])?", re.DOTALL)


def locate(nodeid: str) -> tuple[str, str] | None:
    """(file path, name part) of a node id, or None when it has no ``::`` at all.

    The file boundary is the first ``.py::`` after which the rest reads as ``Name(::Name)*``
    with an optional case id (so a ``.py::`` inside a directory or a case id never matches);
    failing that, the first ``.py`` followed by ``::`` whose prefix has no open ``[``
    (``dir.pyx/`` is not one; a ``.py::`` inside a case id or a ``[`` directory is skipped; a
    stray ``]`` in a directory name is ignored). Without such a boundary the first ``::``
    splits file from name. Name parts never contain ``[``, so the case id starts at the
    first ``[`` after the boundary."""
    for match in re.finditer(r"\.py(?=::)", nodeid):
        if _NAME.fullmatch(nodeid[match.end() + 2 :]):
            return nodeid[: match.end()], nodeid[match.end() + 2 :]
    for match in re.finditer(r"\.py(?=::)", nodeid):
        depth = 0
        for char in nodeid[: match.start()]:
            depth += (char == "[") - (char == "]" and depth > 0)
        if depth == 0:
            return nodeid[: match.end()], nodeid[match.end() + 2 :]
    path, separator, name = nodeid.partition("::")
    return (path, name) if separator else None


def without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Remove the pytest case id (from the first ``[`` of the name) keeping bracketed paths.

    ``all_parts`` is kept for callers; name parts never contain ``[`` so it changes nothing."""
    located = locate(nodeid)
    if located is None:
        # no file boundary: a bare file path stays whole, a bare name loses its case id
        return nodeid if nodeid.endswith(".py") else nodeid.split("[", 1)[0]
    path, name = located
    return path + "::" + name.split("[", 1)[0]


def _params(nodeid: str) -> str:
    start = _case_start(nodeid)
    return nodeid[start + 1 :] if start >= 0 else ""


def _case_start(nodeid: str) -> int:
    """Index of the ``[`` that opens the case id, or -1."""
    located = locate(nodeid)
    if located is None:
        return nodeid.find("[")
    path, name = located
    inside = name.find("[")
    return -1 if inside < 0 else len(path) + 2 + inside


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
        params = _params(node)
        if (
            _case_start(node) >= 0
            and function in functions
            and any(
                _params(other) != params
                for other in present
                if _case_start(other) >= 0 and without_case(other) == function
            )
        ):
            return PER_RUN_NOTE
    return ""


def show(nodeid: str) -> str:
    """Return a bounded, printable node id with absolute paths masked."""
    return mask_paths(nodeid)
