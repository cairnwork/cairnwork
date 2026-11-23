"""Explicit verifier account selection for the verify CLI."""

from __future__ import annotations

import pwd
import sys
from dataclasses import dataclass
from pathlib import Path

from cairnwork.intake import Intake
from cairnwork.intake_request import IntakeRefused
from cairnwork.verifier_account import VerifierAccount
from cairnwork.verifier_root import trusted_account

VERIFIER_ACCOUNTS = {
    "cairnwork-verifier": VerifierAccount(),
    "cairnwork-verifier2": VerifierAccount(
        user="cairnwork-verifier2",
        root=Path(
            "/var/lib/cairnwork-v2"
            if sys.platform.startswith("linux")
            else "/Users/Shared/cairnwork-v2"
        ),
    ),
}


def select_account(name: str) -> VerifierAccount:
    """Resolve a whitelisted account and fail closed if its configured identity is unsafe."""
    account = VERIFIER_ACCOUNTS.get(name)
    if account is None:
        raise IntakeRefused("BAD_VERIFIER_ACCOUNT", f"unknown verifier account: {name}")
    root = account.root
    if not root.is_absolute() or ".." in root.parts:
        raise IntakeRefused("BAD_VERIFIER_ACCOUNT", f"invalid verifier root: {root}")
    try:
        if root.resolve(strict=True) != root or not root.is_dir():
            raise IntakeRefused("BAD_VERIFIER_ACCOUNT", f"unsafe verifier root: {root}")
    except OSError as exc:
        raise IntakeRefused("BAD_VERIFIER_ACCOUNT", f"unavailable verifier root: {root}") from exc
    trusted_account(account)
    try:
        pwd.getpwnam(account.user)
    except KeyError as exc:
        raise IntakeRefused(
            "BAD_VERIFIER_ACCOUNT", f"unknown verifier user: {account.user}"
        ) from exc
    return account


@dataclass(frozen=True)
class SelectedIntake(Intake):
    account: VerifierAccount

    def verifier_account(self) -> VerifierAccount | None:
        return self.account if self.config()["verifier_account"] else None
