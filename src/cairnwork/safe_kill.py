"""One guarded ``killpg`` for every place that ends a child's process group (#1849).

``os.killpg(0)`` is our own group and ``killpg(1)`` / a negative id reaches far past a child; a
group id that is ours ends the caller itself (and, from a shell, its session). None of those is
ever a child's group, so none is sent. A real child started with ``start_new_session=True`` has a
group id equal to its pid and is not ours: it is signalled as before, and the errors
(``ProcessLookupError``, ``PermissionError``) still reach the caller to handle as it did.
"""

from __future__ import annotations

import os


def group_ok(pgid: int) -> bool:
    """Whether ``pgid`` can be some other process's group: above 1 and not this process's own."""
    return pgid > 1 and pgid != os.getpgrp()


def safe_killpg(pgid: int, sig: int) -> bool:
    """``os.killpg(pgid, sig)`` unless ``group_ok`` says no; whether a signal was sent."""
    if not group_ok(pgid):
        return False
    os.killpg(pgid, sig)
    return True
