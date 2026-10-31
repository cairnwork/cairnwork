"""Where and as whom an attempt's verifier and blackbox runs happen (verifiers, blackbox_exec).

The same for every run of the attempt, so it is built once and passed down instead of the seven
values one by one. What changes from run to run (the commit, the specs, the run id, a P2P timeout
or note) stays an argument of the call. ``of`` takes the repository, base commit and import paths
from the task the runs are for.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cairnwork.verifier_account import VerifierAccount


@dataclass(frozen=True)
class VerifyContext:
    repo: Path
    run_root: Path
    attempt_id: str = ""  # marks the runs' git and processes as this attempt's (ATTEMPT_ENV)
    account: VerifierAccount | None = None  # run as this dedicated user (verifier_account.py)
    python: str | None = None  # the verifier Python; None: the account's, else sys.executable
    base_commit: str | None = None  # pytest settings come from this commit (pytest_config.py)
    import_paths: tuple[str, ...] = ()  # checkout-relative dirs prepended to the import path

    @classmethod
    def of(cls, task, run_root, attempt_id="", account=None, python=None) -> VerifyContext:
        return cls(
            task.repo, run_root, attempt_id, account, python, task.base_commit, task.import_paths
        )
