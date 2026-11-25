"""#1802: a process whose name is not valid UTF-8 must still show its owner and start time. They
were read as text, so such a process counted as ours, with a hidden environment, and its start
time could not exclude it: cleanup kept every attempt while it ran."""

import os

from cairnwork import proc_start
from cairnwork.proc_linux import _linux_processes

TICKS = os.sysconf("SC_CLK_TCK")
NAME = b"\xff\xfeab"


def _other_users_process(root, pid, started_seconds):
    base = root / str(pid)
    base.mkdir()
    (base / "status").write_bytes(b"Name:\t" + NAME + b"\nUid:\t4242\t4242\t4242\t4242\n")
    after = ["S", "1", str(pid), "0", "-1"] + ["0"] * 14 + [str(int(started_seconds * TICKS))]
    (base / "stat").write_bytes(str(pid).encode() + b" (" + NAME + b") " + " ".join(after).encode())
    (base / "environ").mkdir()  # cannot be read, as another user's cannot
    (base / "task").mkdir()
    return base


def test_the_owner_of_a_process_with_a_non_utf8_name_is_known(tmp_path):
    _other_users_process(tmp_path, 700, 10.0)
    rows = _linux_processes(str(tmp_path))
    assert rows is not None
    assert [(pid, pgid, uid, env) for pid, pgid, uid, env in rows] == [(700, 700, 4242, None)]


def test_the_start_time_of_a_process_with_a_non_utf8_name_is_known(tmp_path):
    _other_users_process(tmp_path, 701, 10.0)
    assert proc_start.boot_start(701, str(tmp_path)) == 10.0


def test_such_a_process_started_before_the_run_is_not_counted(tmp_path, monkeypatch):
    _other_users_process(tmp_path, 702, 10.0)
    monkeypatch.setattr(proc_start, "LINUX", True)
    monkeypatch.setattr(proc_start, "_boot_id", lambda proc: "boot1")
    assert proc_start.not_older([702], "boot1 1000.0", 5.0, str(tmp_path)) == []
    assert proc_start.not_older([702], "boot1 12.0", 5.0, str(tmp_path)) == [702]
