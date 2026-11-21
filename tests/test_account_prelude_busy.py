"""#832: a busy account is never "unusable before run", and the single busy retry covers the
moment the lock is taken: the prelude runs once per held lock. Fakes only."""

from contextlib import contextmanager
from typing import Any

import pytest

from cairnwork.account_prelude import AccountUnusableBeforeRun, busy_once_more, locked_prelude
from cairnwork.verifier_account import AccountBusy


class BusyAccount:
    """``busy_locks``: how many ``exclusive`` calls raise AccountBusy before one succeeds;
    ``busy_probe``: ``require_idle`` raises AccountBusy (a later change to the prelude)."""

    user = "cairnwork-verifier"

    def __init__(self, busy_locks=0, busy_probe=False):
        self.busy_locks, self.busy_probe, self.events = busy_locks, busy_probe, []

    @contextmanager
    def exclusive(self, timeout=None):
        if self.busy_locks:
            self.busy_locks -= 1
            self.events.append("busy")
            raise AccountBusy("RESOURCE_LEASE_TIMEOUT: another run holds the account")
        self.events.append("lock")
        try:
            yield
        finally:
            self.events.append("unlock")

    def available(self, python=None):
        self.events.append("available")
        return True

    def require_idle(self):
        self.events.append("require_idle")
        if self.busy_probe:
            raise AccountBusy("RESOURCE_LEASE_TIMEOUT: probe saw a busy account")

    def scrub_stale_runs(self):
        self.events.append("scrub")


def _section(account):
    with locked_prelude(account, "python3", 1):
        account.events.append("run")


def test_busy_inside_the_prelude_reaches_the_caller_unchanged():
    account: Any = BusyAccount(busy_probe=True)
    with pytest.raises(AccountBusy) as raised:
        _section(account)
    assert not isinstance(raised.value, AccountUnusableBeforeRun)
    assert account.events == ["lock", "available", "require_idle", "unlock"]  # never ran


def test_the_busy_retry_covers_only_taking_the_lock():
    account: Any = BusyAccount(busy_locks=1)
    busy_once_more(_section, account)
    assert account.events == ["busy", "lock", "available", "require_idle", "scrub", "run", "unlock"]
    assert account.events.count("available") == 1  # the prelude ran once, under one lock


def test_a_second_busy_is_an_error_not_a_third_try():
    account: Any = BusyAccount(busy_locks=2)
    with pytest.raises(AccountBusy):
        busy_once_more(_section, account)
    assert account.events == ["busy", "busy"]
