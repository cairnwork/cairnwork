"""#244: under Linux hidepid the sweep cannot list or read other users' processes, so a sweep
that must see the worker account fails closed (never idle, never gone). Fake mountinfo and a
fake /proc only: no remount, no other account, no sudo."""

import os

import pytest

import cairnwork.confined as confined
from cairnwork import proc_restrict, takeover
from cairnwork.confined import sweep_marked
from cairnwork.ownership import OWNER, OwnershipLedger, TakeoverRefused

PROC = "22 1 0:21 / /proc rw,nosuid,nodev,noexec,relatime shared:12 - proc proc {super}\n"
OTHER = "23 1 0:22 / /sys rw,nosuid shared:7 - sysfs sysfs rw\n"


def _mountinfo(tmp_path, text):
    path = tmp_path / "mountinfo"
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize(
    ("super_options", "expected"),
    [
        ("rw", ""),
        ("rw,hidepid=0", ""),
        ("rw,hidepid=off", ""),
        ("rw,hidepid=1", "hidepid=1 on /proc"),
        ("rw,hidepid=2,gid=1000", "hidepid=2 on /proc"),  # a gid= exemption is not trusted
        ("rw,hidepid=invisible", "hidepid=invisible on /proc"),
    ],
)
def test_the_hidepid_option_of_the_proc_mount_decides(tmp_path, super_options, expected):
    info = _mountinfo(tmp_path, OTHER + PROC.format(super=super_options))
    assert proc_restrict.restriction(info) == expected


def test_the_last_proc_mount_is_the_one_in_effect(tmp_path):
    info = _mountinfo(tmp_path, PROC.format(super="rw") + PROC.format(super="rw,hidepid=2"))
    assert proc_restrict.restriction(info) == "hidepid=2 on /proc"


def test_mount_options_that_cannot_be_read_count_as_restricted(tmp_path):
    """#244 review NIT: no probe can tell an open /proc from, e.g., a PID namespace whose pid 1
    is the owner's own process, so unreadable options fail closed."""
    missing = str(tmp_path / "no-mountinfo")
    assert proc_restrict.restriction(missing) == "mount options of /proc unreadable"
    no_proc_line = _mountinfo(tmp_path, OTHER)  # readable, but no /proc mount listed
    assert proc_restrict.restriction(no_proc_line) == "mount options of /proc unreadable"


def _table(monkeypatch, rows, restricted):
    killed = []
    monkeypatch.setattr(confined, "_processes_with_environment", lambda: list(rows))
    monkeypatch.setattr(confined.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(confined, "_account_uid", lambda name: 4242)
    monkeypatch.setattr(confined, "_restricted", lambda: restricted)
    monkeypatch.setattr(confined, "CONFIRM_WAIT", 0)
    return killed


def test_a_restricted_table_never_shows_the_worker_account_idle(monkeypatch):
    _table(monkeypatch, [], "hidepid=2 on /proc")  # an "empty" account: maybe only hidden
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert not found.listed and not found.gone
    assert found.problem().startswith("SWEEP_UNAVAILABLE: process table restricted (hidepid=2")
    assert sweep_marked("CAIRNWORK_ATTEMPT", "a1").gone  # own processes are never hidden


def test_the_owners_marked_processes_are_killed_before_the_refusal(monkeypatch):
    """#244 review NIT: a hidden account does not spare the owner's own marked processes."""
    mine = (4_000_001, 4_000_001, os.getuid(), {"CAIRNWORK_ATTEMPT=a1"})
    killed = _table(monkeypatch, [mine], "hidepid=2 on /proc")
    found = sweep_marked("CAIRNWORK_ATTEMPT", "a1", account="w")
    assert killed == [4_000_001] and found.killed == 1 and not found.listed
    assert found.problem().startswith("SWEEP_UNAVAILABLE: process table restricted")


def test_the_takeover_says_the_table_is_restricted(tmp_path, monkeypatch):
    ledger = OwnershipLedger(tmp_path / "own.sqlite")
    task_id = ledger.bind("k1", {"a": 1}, OWNER, "card-k1").record.task_id
    ledger.set_status(task_id, "DISPATCHED")
    ledger.set_status(task_id, "FAILED", fence=("a1", "w"))
    _table(monkeypatch, [], "hidepid=1 on /proc")
    with pytest.raises(TakeoverRefused, match="SWEEP_UNAVAILABLE"):
        takeover.supersede(ledger, task_id)
    held = ledger.unswept_attempt(task_id)
    assert held is not None
    assert held.refusal.endswith("kept by: process table restricted (hidepid=1 on /proc)")
    _table(monkeypatch, [], "")
    assert takeover.supersede(ledger, task_id).status == "SUPERSEDED"
