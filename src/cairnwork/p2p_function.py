"""Helpers for interpreting pytest node ids in P2P collection."""

import re

from cairnwork.node_ids import locate, without_case

_IDENT = re.compile(r"[^\W\d]\w*")


def _split(name: str) -> list[str]:
    """The ``::`` parts of a name, a ``::`` inside brackets not splitting."""
    parts, start, depth = [], 0, 0
    for index, char in enumerate(name):
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif name.startswith("::", index) and depth == 0 and (index == 0 or name[index - 1] != ":"):
            parts.append(name[start:index])
            start = index + 2
    return [*parts, name[start:]]


def _well_formed(part: str) -> bool:
    """``Name`` or ``Name[case]``, the case's ``[`` closing exactly at the end of the part (a
    stray ``]`` before the end, as in ``b]`` or ``b[c]]``, is a ``]`` of an earlier case id)."""
    match = _IDENT.match(part)
    if match is None:
        return False
    rest = part[match.end() :]
    if not rest:
        return True
    if not (rest.startswith("[") and rest.endswith("]")):
        return False
    depth = 0
    for index, char in enumerate(rest):
        depth += (char == "[") - (char == "]")
        if depth == 0 and index != len(rest) - 1:
            return False
    return depth == 0


def _without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Remove case suffixes: the last part's (every part's with ``all_parts``). A parametrized
    class keeps its method name (``C[a]::test_y[c]`` -> ``C[a]::test_y``); when the name does not
    split into well-formed parts (a case id holding ``]::``, #1369) the one shared parser,
    ``node_ids.without_case``, decides, so both agree on those ids."""
    located = locate(nodeid)
    if located is None:
        return nodeid  # a bare name or file path is returned whole
    path, name = located
    parts = _split(name)
    if not all(map(_well_formed, parts)):
        return without_case(nodeid)
    last = len(parts) - 1
    kept = [
        part.split("[", 1)[0] if all_parts or index == last else part
        for index, part in enumerate(parts)
    ]
    return "::".join([path, *kept[:last], kept[last].split("[", 1)[0]])


def _function(nodeid: str) -> str:
    return _without_case(nodeid, all_parts=True)
