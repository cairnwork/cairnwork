"""Who may write a verifier account's root (#1382)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cairnwork.intake_request import IntakeRefused
from cairnwork.textclean import shown
from cairnwork.verifier_account import VerifierAccount


def root_trust_problem(root: Path) -> str | None:
    """Why ``root`` cannot be trusted as a verifier root (None = fine, or it does not exist).

    setup_verifier_user*.sh makes it ``owner:group`` mode 2750: only the owner (this process's
    uid) writes it. Another owner, or a group or other write bit, lets someone else plant the
    lock, queue and policy files the owner then trusts (#1382). lstat: a link is refused
    elsewhere; here it would be judged by itself, never by its target."""
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None  # a missing root is refused by the caller that needs it
    except OSError as exc:
        return f"cannot stat {root}: {exc.strerror}"
    if not stat.S_ISDIR(info.st_mode):
        return f"{root} is not a real directory (a link or file is refused)"
    if info.st_uid != os.getuid():
        return f"{root} is owned by uid {info.st_uid}, not by this user"
    if info.st_mode & 0o022:
        return f"{root} is writable by group or other (mode {info.st_mode & 0o7777:o})"
    return None


def trusted_account(account: VerifierAccount) -> VerifierAccount:
    """``account`` itself, or BAD_VERIFIER_ACCOUNT when its root is not ours alone to write."""
    problem = root_trust_problem(account.root)
    if problem is not None:
        raise IntakeRefused("BAD_VERIFIER_ACCOUNT", f"untrusted verifier root: {shown(problem)}")
    return account
