"""Helpers for interpreting pytest node ids in P2P collection."""

from cairnwork.node_ids import locate


def _without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Remove case suffixes while preserving bracketed paths and nested node boundaries."""
    located = locate(nodeid)
    if located is None:
        return nodeid  # a bare name or file path is returned whole
    path, test_id = located
    parts = []
    start = depth = 0
    index = 0
    while index < len(test_id):
        char = test_id[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif test_id.startswith("::", index) and depth == 0:
            parts.append(test_id[start:index])
            start = index + 2
            index += 1
        index += 1
    parts.append(test_id[start:])
    for index in range(0 if all_parts else len(parts) - 1, len(parts)):
        parts[index] = parts[index].split("[", 1)[0]
    return "::".join([path, *parts])


def _function(nodeid: str) -> str:
    return _without_case(nodeid, all_parts=True)
