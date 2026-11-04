"""The checks before a run as the verifier account, under its lock, and what a refusal there means
in the candidate stage (#803).

The prelude (``available``, ``require_idle``, ``scrub_stale_runs``) runs before any code of the
run (``verifiers.run_verifiers``). In the worker pipeline the candidate's verifier run is the first
account section of its judging (a blackbox run is verify only: pipeline._require_no_blackbox), so
a refusal there means no candidate code ran as the account in this attempt: no evidence about the
candidate, and an isolated worker's attempt ends UNCOMPARABLE ``VERIFIER_ACCOUNT_UNUSABLE`` as in
the base stage. A refusal at a later section (the candidate already ran as the account) stays an
infrastructure error: the candidate may have caused it. ``AccountBusy`` is never no evidence
(another task's candidate can hold the lock): the first section is asked once more, then it is an
error as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from cairnwork.verifier_account import AccountBusy, VerifierAccountError


class AccountUnusableBeforeRun(VerifierAccountError):
    """A run's prelude refused the account; nothing of the run has started."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@contextmanager
def locked_prelude(account, python: str, timeout: float) -> Iterator[None]:
    """Hold ``account.exclusive(timeout)`` for the run in the block, after the prelude passed. Only
    the prelude's refusals become ``AccountUnusableBeforeRun``, never an error of the run."""
    with account.exclusive(timeout):
        try:
            if not account.available(python):
                raise VerifierAccountError(f"account {account.user} is not usable")
            account.require_idle()
            account.scrub_stale_runs()  # a killed run's private/run, before this run creates one
        except VerifierAccountError as exc:
            raise AccountUnusableBeforeRun(str(exc).removeprefix(f"{exc.code}: ")) from exc
        yield


def after_candidate_ran(call, *args, **kwargs):
    """A section after the candidate ran as the account: its prelude refusal is a plain error."""
    try:
        return call(*args, **kwargs)
    except AccountUnusableBeforeRun as exc:
        raise VerifierAccountError(exc.detail) from exc


def busy_once_more(call, *args, **kwargs):
    """``AccountBusy`` is raised by ``exclusive()``, before the run creates anything: ask again."""
    try:
        return call(*args, **kwargs)
    except AccountBusy:
        return call(*args, **kwargs)
