"""What is still on disk after a dead run's cleanup, for the owner's and the account's roots."""

from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path

from cairnwork import cleanup_sweep
from cairnwork.confined import ATTEMPT_ENV, confirm_gone
from cairnwork.recovery import (
    _leftover_names,
    _remove_account_leftovers,
    _remove_leftovers,
    path_absent,
)


def remaining(run_root: Path, account, attempt_id: str) -> list[str]:
    """Remove the account's own run directories (it alone can), then re-scan both roots from
    disk: the names of the attempt's directories that are still there."""
    if account is not None:
        _remove_account_leftovers(account, attempt_id)
    left = _leftover_names(run_root, attempt_id)
    if account is not None:
        left += _leftover_names(account.run_root, attempt_id)
    return left


def dead_run_look(sweep, attempt_id: str, run_root: Path, account, mark: str):
    """The sweep of a dead run's processes (#1827): names the verifier account and, taken before
    cleanup removes them, the run's directories."""
    dirs = cleanup_sweep.run_dirs(attempt_id, run_root, account)
    return partial(
        sweep, ATTEMPT_ENV, attempt_id, dispatched=mark, cwd_dirs=dirs,
        account=cleanup_sweep.user_of(account),
    )  # fmt: skip


def attempt_done(sweep, scratch: Path, run_root: Path, account, attempt_id: str, mark: str) -> bool:
    """Clean one dead attempt; True only when its processes are shown gone, its scratch and its
    directories (the owner's and the account's) are gone."""
    look = dead_run_look(sweep, attempt_id, run_root, account, mark)
    swept = confirm_gone(look(), look)
    _remove_leftovers(run_root, attempt_id)
    shutil.rmtree(scratch, ignore_errors=True)
    return swept.gone and path_absent(scratch) and not remaining(run_root, account, attempt_id)
