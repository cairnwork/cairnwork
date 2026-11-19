"""Where a pytest node id splits into file and name (#1272); imports nothing of Cairnwork, so any
module (``verdict_record`` included) can use it.

Scope: ids pytest itself produced, and verifier targets that already passed the ``test_target``
grammar. The boundary is not pytest's own "first ``::``"; it differs only when the file path itself
holds ``::``, where pytest's id is ``a::b/t.py::test`` and the first-``::`` reading is the wrong one
(#1499). An arbitrary string outside those two sources is not promised the same split."""

from __future__ import annotations

import re

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


def file_part(nodeid: str) -> str:
    """The file path of a node id (the whole id when it has no ``::``), cut at the file boundary
    ``locate`` finds, not at the first ``::``: a path may itself hold ``::`` (#1272)."""
    located = locate(nodeid)
    return nodeid if located is None else located[0]


def name_part(nodeid: str) -> str:
    """The part after the file (``Class::name[case]``), or "" when the id has no ``::``."""
    located = locate(nodeid)
    return "" if located is None else located[1]
