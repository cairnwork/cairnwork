"""Repo-root pytest hooks. The kill guard lives here, not in tests/conftest.py, which is a pinned
trusted file (tests/test_1039_1353_witnesses.py PINS): a change there needs a PIN-APPROVE."""

import os
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _tests_signal_only_their_own_processes(monkeypatch):
    """#1849: os.kill / os.killpg with a non-zero signal reach only this test's own processes
    (see tests/kill_guard.py). A test that fakes them itself replaces the guard for its duration."""
    import kill_guard

    guard = kill_guard.Guard(os.kill, os.killpg)
    monkeypatch.setattr(kill_guard, "ACTIVE", [guard])
    monkeypatch.setattr(os, "kill", guard.kill)
    monkeypatch.setattr(os, "killpg", guard.killpg)
    monkeypatch.setattr(subprocess.Popen, "__init__", guard.record(subprocess.Popen.__init__))
