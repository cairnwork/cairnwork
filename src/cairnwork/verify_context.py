"""Where and as whom an attempt's verifier and blackbox runs happen (verifiers, blackbox_exec).

The same for every run of the attempt, so it is built once and passed down instead of the seven
values one by one. What changes from run to run (the commit, the specs, the run id, a P2P timeout
or note) stays an argument of the call. ``of`` takes the repository, base commit and import paths
from the task the runs are for.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from cairnwork.verifier_account import VerifierAccount


def new_hash_seed() -> str:
    """Draw one interpreter hash seed for an attempt (never the public blackbox seed)."""
    return str(secrets.randbelow(2**32))


@dataclass(frozen=True)
class VerifyContext:
    repo: Path
    run_root: Path
    attempt_id: str = ""  # marks the runs' git and processes as this attempt's (ATTEMPT_ENV)
    account: VerifierAccount | None = None  # run as this dedicated user (verifier_account.py)
    python: str | None = None  # the verifier Python; None: the account's, else sys.executable
    base_commit: str | None = None  # pytest settings come from this commit (pytest_config.py)
    import_paths: tuple[str, ...] = ()  # checkout-relative dirs prepended to the import path
    hash_seed: str = field(default_factory=new_hash_seed)  # PYTHONHASHSEED of every run (#870)

    @classmethod
    def of(cls, task, run_root, attempt_id="", account=None, python=None) -> VerifyContext:
        return cls(
            task.repo, run_root, attempt_id, account, python, task.base_commit, task.import_paths
        )

    def started(self, attempt_id: str) -> VerifyContext:
        """This context for a new attempt: a fresh hash seed that every run of the attempt (base,
        head, P2P) shares, kept for replay in owner-only state, never in the request, receipt or
        checkout (#870 item 2)."""
        ctx = replace(self, attempt_id=attempt_id, hash_seed=new_hash_seed())
        record_hash_seed(ctx)
        return ctx


def record_hash_seed(ctx: VerifyContext) -> None:
    """Write ``<run_root>/../verifier-seeds/<attempt_id>.json`` (0600, new file only)."""
    directory = Path(ctx.run_root).parent / "verifier-seeds"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / f"{ctx.attempt_id}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"attempt_id": ctx.attempt_id, "hash_seed": ctx.hash_seed}, stream)
        stream.write("\n")


def verifier_interpreter(ctx: VerifyContext) -> str:
    """The Python the verifier runs: ``ctx.python``, else the account's, else this process's.

    One expression for every reader (verifiers.run_verifiers, the tamper scan's parse of the
    files the verifier will import), so what is scanned is read by the interpreter that imports it.
    """
    if ctx.python:
        return ctx.python
    return ctx.account.python if ctx.account is not None else sys.executable
