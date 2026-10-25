"""Cleanup of a shared run root, scoped to what one test created (Issue #247).

The account's run root is shared with other sessions' live verify runs: a test removes only
names that start with a prefix it registered (a unique attempt id of its own), and holds the
account lock (``exclusive``, the one live runs use) while it does. It never removes "whatever
is new": that deleted other sessions' live run directories.
"""

import contextlib
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from cairnwork.journal import Journal


def _reopen(path: Path) -> None:
    """A failed test may leave owner directories shut (0o700): reopen them for the account rm."""
    for top, dirs, _ in os.walk(path):
        for entry in [top, *(os.path.join(top, d) for d in dirs)]:
            if not os.path.islink(entry) and os.lstat(entry).st_uid == os.getuid():
                os.chmod(entry, os.lstat(entry).st_mode | 0o777)


def remove_own(run_root: Path, account, prefixes: list[str]) -> None:
    if not prefixes or not run_root.is_dir():
        return
    mine = sorted(n for n in os.listdir(run_root) if any(n.startswith(p) for p in prefixes))
    if not mine:
        return
    with account.exclusive():
        for name in mine:
            path = run_root / name
            _reopen(path)
            account.remove(path)
            shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def own_run_names(
    run_root: Path, account, prefixes: list[str] | None = None
) -> Iterator[list[str]]:
    """Yield a list the test appends its name prefixes to; remove only those names afterwards.
    A module that registers names through its own helper passes that list in (emptied here), so
    teardown reads the very list the test appended to, not a copy."""
    prefixes = [] if prefixes is None else prefixes
    prefixes.clear()
    try:
        yield prefixes
    finally:
        remove_own(run_root, account, prefixes)


def own_run_id(prefixes: list[str], prefix: str = "t") -> str:
    """A unique attempt id for a test, registered in ``prefixes`` so its cleanup removes it."""
    run_id = prefix + os.urandom(6).hex()
    prefixes.append(f"{run_id}-")
    return run_id


def own_attempt(state: Path) -> str | None:
    """The attempt id of the run a test started, from its own journal (``state``): another
    session's run in a shared run root has other attempt ids."""
    path = state / "journal.sqlite"
    if not path.exists():
        return None
    try:
        entries = Journal(path).entries("k1")
    except sqlite3.Error:  # the driver is still creating it
        return None
    return entries[-1].attempt_id if entries else None
