"""Helpers for interpreting pytest node ids in P2P collection."""

from cairnwork.node_ids import locate, without_case


def _without_case(nodeid: str, *, all_parts: bool = False) -> str:
    """Remove the case suffix; the one parser is ``node_ids.without_case`` (#1369), so a case id
    holding ``]::`` is cut the same everywhere. ``all_parts`` is kept for callers (no-op)."""
    if locate(nodeid) is None:
        return nodeid  # a bare name or file path is returned whole
    return without_case(nodeid, all_parts=all_parts)


def _function(nodeid: str) -> str:
    return _without_case(nodeid, all_parts=True)
