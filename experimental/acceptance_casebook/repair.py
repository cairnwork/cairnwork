"""Controlled recovery after a crash between the append and the head update (#772 P1-A).

The only state repaired: the chain is intact and the head names the line before the last one
(exactly one line was appended, its head update was lost). ``repair`` then appends a
``"type": "repair"`` line recording the stale and the adopted head, so the recovery stays visible.
Anything else (edited line, partial last line, head naming no line) stays refused, fail closed.
"""

from __future__ import annotations

from pathlib import Path

from experimental.acceptance_casebook.store import (
    ZERO,
    CasebookError,
    _append,
    _head,
    _read_lines,
    locked,
)


def repair(path: Path) -> str:
    """Returns "CLEAN" when nothing needs repair, else "REPAIRED"; raises when not repairable."""
    return locked(path, lambda: _repair_locked(path))


def _repair_locked(path: Path) -> str:
    entries, last = _read_lines(path)  # an edited or partial line raises here
    head = _head(path)
    recorded = head.read_text().strip() if head.exists() else ZERO
    if recorded == last:
        return "CLEAN"
    before_last = entries[-2][1] if len(entries) >= 2 else ZERO
    if not entries or recorded != before_last:
        raise CasebookError("CASEBOOK_NOT_APPEND_ONLY", "head is not one line behind; not repaired")
    _append(path, {"type": "repair", "stale_head": recorded, "adopted_head": last}, last)
    return "REPAIRED"
