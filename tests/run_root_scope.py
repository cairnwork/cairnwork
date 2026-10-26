"""Cleanup of a shared run root, scoped to what one test created (Issue #247).

The account's run root is shared with other sessions' live verify runs: a test removes only
names that start with a prefix it registered (a unique attempt id of its own), and holds the
account lock (``exclusive``, the one live runs use) while it does. It never removes "whatever
is new": that deleted other sessions' live run directories.
"""

import contextlib
import dataclasses
import os
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path


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
    session's run in a shared run root has other attempt ids. Read-only and by the journal's
    latest row, since the journal keys rows by the generated task id, not the idempotency key."""
    path = state / "journal.sqlite"
    if not path.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT attempt_id FROM attempt_journal ORDER BY seq DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError as error:  # the driver is still creating or writing it
        if "no such table" in str(error) or "locked" in str(error):
            return None
        raise  # anything else is reported, not waited out until the caller's deadline
    return row[0] if row else None


def lock_held(account):
    """A copy of ``account`` for code run while the test already holds ``account.exclusive()``:
    the lock is not reentrant, so the inner run must not wait on it again (Issue #247). A test
    that starts an account process outside a run holds the lock while it lives, or another
    session's locked run would count it as its own leftover."""

    class LockHeld(type(account)):
        def exclusive(self, timeout=None):
            return contextlib.nullcontext()

    init = {f.name: getattr(account, f.name) for f in dataclasses.fields(account) if f.init}
    return LockHeld(**init)
