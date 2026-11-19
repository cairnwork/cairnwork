"""Helpers for interpreting pytest node ids in P2P collection."""

from cairnwork.node_ids import without_case_by_part


def _without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """The parser is ``node_ids.without_case_by_part`` (#1369, #1276)."""
    return without_case_by_part(nodeid, all_parts=all_parts)


def _function(nodeid: str) -> str:
    return _without_case(nodeid, all_parts=True)
