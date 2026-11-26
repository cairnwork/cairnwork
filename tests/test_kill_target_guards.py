"""Kill-target guards (#1849): bad kill targets send nothing, and tests may signal only their own.

Every call is a recorder: no process is ever signalled and no sudo is run.
"""

import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from kill_guard import KillGuardError

from cairnwork import child_run, verifier_account
from cairnwork.verifier_account import VerifierAccount

BAD_UIDS = {"self": os.getuid(), "root": 0}


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: recorded.append(a) or SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(VerifierAccount, "_escaped_count", lambda self, deadline=None, **k: 0)
    return recorded


@pytest.mark.parametrize("who", ["self", "root"])
def test_no_sudo_kill_runs_as_the_owner_or_root(monkeypatch, calls, who):
    uid = BAD_UIDS[who]
    monkeypatch.setattr(verifier_account.pwd, "getpwnam", lambda _u: SimpleNamespace(pw_uid=uid))
    left = VerifierAccount(user=f"user-{who}")._kill_until_clean(3, time.monotonic() + 30)
    assert calls == [] and left == -1


def test_an_unknown_account_gets_no_kill(monkeypatch, calls):
    def missing(_user):
        raise KeyError("no such user")

    monkeypatch.setattr(verifier_account.pwd, "getpwnam", missing)
    assert VerifierAccount(user="nobody-here")._kill_until_clean(3, time.monotonic() + 30) == -1
    assert calls == []


def test_a_real_account_still_gets_the_kill(monkeypatch, calls):
    monkeypatch.setattr(
        verifier_account.pwd, "getpwnam", lambda _u: SimpleNamespace(pw_uid=os.getuid() + 7)
    )
    assert VerifierAccount(user="other")._kill_until_clean(3, time.monotonic() + 30) == 0
    assert len(calls) == 1 and "-9" in calls[0][0]


@pytest.mark.parametrize("group", [0, 1, -1, os.getpgrp()])
def test_child_run_never_signals_a_shared_group(monkeypatch, group):
    sent = []
    monkeypatch.setattr(child_run.os, "killpg", lambda g, s: sent.append((g, s)))
    child_run._kill_child_group(group)
    assert sent == []


def test_child_run_signals_a_group_of_its_own(monkeypatch):
    sent = []
    monkeypatch.setattr(child_run.os, "killpg", lambda g, s: sent.append((g, s)))
    other = os.getpgrp() + 5
    child_run._kill_child_group(other)
    assert sent == [(other, signal.SIGKILL)]


@pytest.mark.parametrize("pid", [1, 0, -1, os.getppid()])
def test_the_guard_refuses_a_foreign_pid(pid):
    with pytest.raises(KillGuardError):
        os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("group", [0, 1, -1, os.getpgrp()])
def test_the_guard_refuses_a_foreign_group(group):
    with pytest.raises(KillGuardError):
        os.killpg(group, signal.SIGKILL)


def test_the_guard_lets_signal_zero_and_own_children_through():
    os.kill(os.getpid(), 0)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    leader = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    os.kill(child.pid, signal.SIGKILL)
    os.killpg(leader.pid, signal.SIGKILL)
    assert child.wait(timeout=10) < 0 and leader.wait(timeout=10) < 0
