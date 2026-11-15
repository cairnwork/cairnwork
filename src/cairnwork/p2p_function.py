"""Helpers for interpreting pytest node ids in P2P collection."""

from cairnwork.node_ids import without_case


def _without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Compatibility wrapper for the shared node id parser."""
    return without_case(nodeid, all_parts=all_parts)


def _function(nodeid: str) -> str:
    return _without_case(nodeid, all_parts=True)
