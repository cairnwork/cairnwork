"""The collection-time account probe holds the account lock (Issue #247).

``VerifierAccount.available()`` starts a process as the shared account. Started outside the lock,
it shows up in another session's locked run, whose reap then counts it as "1 process(es) outlived
the run" (and kills it, so this session's probe reads the account as unusable). These tests drive
the conftest hook with a stand-in account class, so they need neither sudo nor the account.
"""

import contextlib
import getpass

import conftest
import pytest

import cairnwork.verifier_account as va


class _Item:
    def __init__(self, *marks):
        self.keywords = dict.fromkeys(marks, True)
        self.added = []

    def add_marker(self, marker):
        self.added.append(marker)


def _fake_account(events, busy=False, usable=True, user=None):
    class Account:
        def __init__(self):
            self.user = user or getpass.getuser()

        @contextlib.contextmanager
        def exclusive(self, timeout=None):
            if busy:
                raise va.AccountBusy("RESOURCE_LEASE_TIMEOUT: busy")
            events.append("lock")
            try:
                yield
            finally:
                events.append("unlock")

        def available(self, python=None):
            events.append("probe")
            return usable

    return Account


def _collect(monkeypatch, account, items):
    monkeypatch.setattr(va, "VerifierAccount", account)
    monkeypatch.delenv("CAIRNWORK_REQUIRE_VERIFIER_ACCOUNT", raising=False)
    monkeypatch.delenv("CAIRNWORK_REQUIRE_SANDBOX", raising=False)
    conftest.pytest_collection_modifyitems(None, items)


def _skip_reasons(item):
    return [m.kwargs.get("reason", "") for m in item.added if m.name == "skip"]


def test_the_probe_runs_inside_the_account_lock(monkeypatch):
    events = []
    item = _Item("verifier_account")
    _collect(monkeypatch, _fake_account(events), [item])
    assert events == ["lock", "probe", "unlock"]
    assert not any("cairnwork-verifier" in r for r in _skip_reasons(item))


def test_no_probe_when_no_account_test_is_collected(monkeypatch):
    events = []
    _collect(monkeypatch, _fake_account(events), [_Item("sandbox"), _Item()])
    assert events == []  # nothing selected needs the account: start nothing as it


def test_a_busy_account_skips_its_tests_locally(monkeypatch):
    item = _Item("verifier_account")
    _collect(monkeypatch, _fake_account([], busy=True), [item])
    assert any("busy" in r for r in _skip_reasons(item))


def test_a_busy_account_is_an_error_where_the_account_is_required(monkeypatch):
    monkeypatch.setattr(va, "VerifierAccount", _fake_account([], busy=True))
    monkeypatch.setenv("CAIRNWORK_REQUIRE_VERIFIER_ACCOUNT", "1")
    with pytest.raises(pytest.UsageError, match="busy"):
        conftest.pytest_collection_modifyitems(None, [_Item("verifier_account")])


def test_an_unusable_account_still_skips_its_tests(monkeypatch):
    events = []
    item = _Item("verifier_account")
    _collect(monkeypatch, _fake_account(events, usable=False), [item])
    assert events == ["lock", "probe", "unlock"]
    assert any("cairnwork-verifier" in r for r in _skip_reasons(item))


def test_an_unknown_user_is_not_locked_or_probed(monkeypatch):
    events = []
    item = _Item("verifier_account")
    _collect(monkeypatch, _fake_account(events, user="cairnwork-no-such-user-247"), [item])
    assert events == []
    assert any("cairnwork-verifier" in r for r in _skip_reasons(item))
