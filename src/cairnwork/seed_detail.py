"""The refusal text of a base run whose failures differ between two seeds (BASE_SEED_SENSITIVE)."""

from __future__ import annotations

from cairnwork.node_ids import show, unmatched_note


def seed_sensitive_detail(
    changed, first_failed, second_failed, first_seed, second_seed, comparison, hidden=False
):
    """Bound candidate-controlled node IDs before placing them in a refusal detail. A run of a
    not-yet-published test commit (``hidden``, #1186) reports how many, never which."""
    ids = sorted(set(changed) | set(first_failed) | set(second_failed))[:20]
    quoted = f"{len(ids)} test(s)" if hidden else [_quoted_node_id(node) for node in ids]
    detail = (
        f"failed node IDs differ or second run was invalid at seeds "
        f"seed {first_seed} and seed {second_seed}: {quoted}"
    )
    note = unmatched_note(set(changed) | set(first_failed), set(second_failed))
    if note:
        detail += f"; {note}"
    if comparison.kind != "TEETH":
        detail += f"; second run: {comparison.kind} {comparison.code}"
    return detail


def _quoted_node_id(node: str) -> str:
    """Return a repr-quoted node ID no longer than 200 characters."""
    candidate = show(node)
    while len(repr(candidate)) > 200:
        candidate = candidate[: max(0, len(candidate) - (len(repr(candidate)) - 199))]
    return repr(candidate)
