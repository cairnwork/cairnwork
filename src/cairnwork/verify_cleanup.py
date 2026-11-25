"""Small pieces of the verify run's orphan cleanup (#1796, #1802): what a marker and the
filesystem may be trusted for. Strict rule throughout: what cannot be read or seen is not gone."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from cairnwork import proc_start
from cairnwork.intake_request import IntakeRefused
from cairnwork.recovery import _leftover_names, _remove_account_leftovers
from cairnwork.verifier_account import VerifierAccountError

_CANONICAL = re.compile(r"[0-9]+(\.[0-9]+)?", re.ASCII)  # one spelling: what boot_mark writes


def path_absent(path: Path) -> bool:
    """True only when lstat says nothing is there (no such file, or a parent that is not a
    directory: nothing can exist below it). Any other error is "cannot see", never "gone"."""
    try:
        os.lstat(path)
    except OSError as exc:
        return isinstance(exc, (FileNotFoundError, NotADirectoryError))
    return False


def write_start_mark(fd: int) -> None:
    """Note in a run's held marker when it started (boot mark, "" off Linux), as DISPATCHED does."""
    with suppress(OSError):  # no mark: cleanup keeps the strict rule
        os.write(fd, proc_start.boot_mark().encode())


def read_start_mark(fd: int) -> str:
    """The marker's start mark, or "" (strict rule) unless it is ``"<this boot's id> <seconds>"``
    in the one canonical spelling with 0 <= seconds <= now: the file is not trusted, a mark from
    the future hides survivors."""
    try:
        text = os.pread(fd, 256, 0).decode("utf-8", "replace")
        mine, now = text.split(" "), proc_start.boot_mark().split(" ")
        ok = (
            len(mine) == len(now) == 2
            and mine[0] == now[0]
            and _CANONICAL.fullmatch(mine[1]) is not None
            and 0 <= float(mine[1]) <= float(now[1])
        )
    except (OSError, ValueError):
        return ""
    return text if ok else ""


def leftovers(run_root: Path, attempt_id: str) -> list[str]:
    """Names (only) still under ``run_root`` after the attempt's removal: clones, verifier
    directories and snapshot indexes. Looked for on disk again; the removers are not trusted."""
    return _leftover_names(run_root, attempt_id)


def clear_account(intake: Any, attempt_id: str) -> list[str]:
    """Remove the attempt's directories under the verifier account's own run root and name what
    is still there. With no account configured there is nothing to look at; an account whose
    root cannot be checked is reported as a leftover, never as clean."""
    try:
        account = intake.verifier_account()
        if account is None:
            return []
        _remove_account_leftovers(account, attempt_id)
        if path_absent(account.run_root):
            return []
        if not account.run_root.is_dir():
            return ["verifier-account-root-unreadable"]
        return leftovers(account.run_root, attempt_id)
    except (
        IntakeRefused,
        VerifierAccountError,
        OSError,
        ValueError,
    ):  # an untrusted or unreadable account root: kept, retried later
        return ["verifier-account-root-unreadable"]
