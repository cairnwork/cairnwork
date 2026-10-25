"""#244: the processes named in a takeover refusal (proc_names.describe) and, under Linux
hidepid=1, whose they are (confined._hidden_owner). Names come from processes the attempt may
control, so they are cut to a short safe word. Fake /proc for the owner; no other account,
no sudo, no remount."""

import os
import subprocess
import time

import pytest
from test_sweep_result import FAKE, ME, OTHER_UID, _proc

import cairnwork.confined as confined
from cairnwork import proc_names
from cairnwork.confined import sweep_marked


def test_describe_reads_real_command_names():
    assert "python" in proc_names.describe((os.getpid(),)).lower()


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("/usr/bin/ssh-agent\n", "ssh-agent"),
        ("python3 -m x --password=hunter2", "python3"),
        ("/opt/" + "x" * 6000 + "--password=hunter2", "x" * proc_names.NAME_CHARS),
        ("evil;rm\x1b[31m", "evil?rm??31m"),
        ("", "?"),
        ("   ", "?"),
    ],
)
def test_names_are_cut_to_a_short_safe_word(raw, safe):
    assert proc_names.safe_name(raw) == safe


def test_a_hostile_argv0_does_not_reach_the_refusal():
    """#244 review MUST-FIX: ps -o comm= prints argv[0] verbatim, so a 6,000-character argv[0]
    ending in a secret came back whole. A real child, renamed with exec -a."""
    secret = "--password=" + "s3cr3t" * 4
    argv0 = "/tmp/" + "w" * 6000 + " " + secret
    child = subprocess.Popen(["/bin/bash", "-c", 'exec -a "$0" /bin/sleep 30', argv0])
    try:
        shown = ""
        for _ in range(100):  # until exec has happened and ps shows the new argv[0]
            shown = proc_names.describe((child.pid,))
            if "w" in shown:
                break
            time.sleep(0.05)
        assert "w" * proc_names.NAME_CHARS in shown and "s3cr3t" not in shown
        assert len(shown) <= len(str(child.pid)) + 1 + proc_names.NAME_CHARS
    finally:
        child.kill()
        child.wait()


def _hidden_status(tmp_path, pid, uid):
    base = _proc(tmp_path, pid, uid)
    for name in ("status", "stat", "environ"):
        (base / name).chmod(0)
    return base


@pytest.mark.skipif(ME == 0, reason="root reads every file")
@pytest.mark.parametrize(
    ("dir_owner", "signal_0", "uid"),
    [
        (OTHER_UID, None, OTHER_UID),  # dumpable: its directory names its owner
        (0, "denied", confined.FOREIGN),  # not dumpable, or root's: not the owner's
        (0, "delivered", ME),  # not dumpable, but the owner may signal it: the owner's
    ],
)
def test_under_hidepid_1_the_owner_is_read_from_the_directory(
    tmp_path, monkeypatch, dir_owner, signal_0, uid
):
    """#244 review NIT: hidepid=1 hides other users' status, so every same-user recover read
    them as the owner's hidden processes (UNREADABLE_PROCESSES)."""
    base = _hidden_status(tmp_path, FAKE, OTHER_UID)
    real_kill = os.kill

    def kill(pid, sig):
        if pid != FAKE:
            return real_kill(pid, sig)
        if signal_0 == "denied":
            raise PermissionError

    monkeypatch.setattr(confined, "_dir_owner", lambda path: dir_owner)
    monkeypatch.setattr(confined.os, "kill", kill)
    try:
        rows = confined._linux_processes(str(tmp_path))
    finally:
        for name in ("status", "stat", "environ"):
            (base / name).chmod(0o600)
    assert rows == [(FAKE, None, uid, None)]
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: list(rows or []))
    monkeypatch.setattr(confined, "_reread", lambda pids: dict.fromkeys(pids))
    monkeypatch.setattr(confined, "HIDDEN_WAIT", 0)
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").gone is (uid != ME)
    monkeypatch.setattr(confined, "_account_uid", lambda name: OTHER_UID)
    monkeypatch.setattr(confined, "_restricted", lambda: "")  # pretend: only the rows matter
    with_account = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert not with_account.gone  # the account's, or the owner's hidden one, or unknown
