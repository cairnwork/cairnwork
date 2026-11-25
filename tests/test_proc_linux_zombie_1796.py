"""#1796: a zombie runs nothing, so the Linux process table leaves it out, as the macOS one does.
Its environ cannot be opened by a user who is not root, which made a killed, unreaped child look
like a hidden live process and kept the attempt. A process whose main thread has exited but whose
other threads still run also shows state Z on its own stat; it is alive and stays in the table."""

import os

import pytest

from cairnwork.proc_linux import _linux_processes


def _proc(root, pid, state, environ=b"A=1\0", comm: str | bytes = "x", tasks=None):
    """A fake /proc/<pid>. tasks: {tid: state} written under task/<tid>/stat; None writes none."""
    base = root / str(pid)
    base.mkdir()
    (base / "status").write_text("Name:\tx\nUid:\t1000\t1000\t1000\t1000\n")
    (base / "stat").write_bytes(_stat(pid, state, comm))
    (base / "environ").write_bytes(environ)
    for tid, tstate in (tasks or {}).items():
        task = base / "task" / str(tid)
        task.mkdir(parents=True)
        (task / "stat").write_bytes(_stat(tid, tstate, comm))
    return base


def _stat(pid, state, comm: str | bytes):
    comm = comm if isinstance(comm, bytes) else comm.encode()
    return (
        str(pid).encode() + b" (" + comm + b") " + state.encode() + b" 1 %d %d 0 -1\n" % (pid, pid)
    )


def _pids(root):
    table = _linux_processes(str(root))
    assert table is not None
    return sorted(row[0] for row in table)


def test_a_zombie_is_left_out_of_the_table(tmp_path):
    _proc(tmp_path, 10, "Z")
    _proc(tmp_path, 11, "S")
    assert _pids(tmp_path) == [11]


def test_a_sleeping_process_stays_in_the_table(tmp_path):
    _proc(tmp_path, 12, "S")
    assert _pids(tmp_path) == [12]


@pytest.mark.parametrize(
    "tasks",
    [{20: "Z"}, {20: "Z", 21: "Z"}, None],
    ids=["leader-only", "all-threads-zombie", "no-task-dir"],
)
def test_a_process_with_every_thread_zombie_is_left_out(tmp_path, tasks):
    _proc(tmp_path, 20, "Z", tasks=tasks)
    assert _pids(tmp_path) == []


@pytest.mark.parametrize("live", ["S", "R", "D", "T"])
def test_a_process_whose_main_thread_ended_but_another_thread_runs_stays(tmp_path, live):
    """Main thread Z, a second thread alive, environ unreadable: still in the table, no env."""
    base = _proc(tmp_path, 30, "Z", tasks={30: "Z", 31: live})
    os.chmod(base / "environ", 0)
    if os.access(base / "environ", os.R_OK):
        (base / "environ").unlink()  # running as root: make it unopenable another way
        (base / "environ").mkdir()
    table = _linux_processes(str(tmp_path))
    assert table is not None
    assert [(row[0], row[3]) for row in table] == [(30, None)]


def test_a_process_with_a_live_thread_stays_even_if_its_own_stat_says_z_and_no_leader_task(
    tmp_path,
):
    _proc(tmp_path, 32, "Z", tasks={33: "S"})
    assert _pids(tmp_path) == [32]


def test_a_command_name_that_looks_like_a_state_does_not_hide_the_real_state(tmp_path):
    _proc(tmp_path, 40, "S", comm="a) Z (b", tasks={40: "S"})
    _proc(tmp_path, 41, "S", comm="a) Z (b")
    assert _pids(tmp_path) == [40, 41]


def test_a_command_name_with_a_bracket_does_not_hide_a_real_zombie(tmp_path):
    _proc(tmp_path, 42, "Z", comm="a) S (b", tasks={42: "Z"})
    assert _pids(tmp_path) == []


def test_a_stat_with_bytes_that_are_not_utf8_does_not_raise(tmp_path):
    _proc(tmp_path, 50, "S", comm=b"\xff\xfeab", tasks={50: "S"})
    _proc(tmp_path, 51, "Z", comm=b"\xff\xfeab", tasks={51: "Z"})
    assert _pids(tmp_path) == [50]


def test_a_missing_stat_does_not_hide_the_process(tmp_path):
    base = _proc(tmp_path, 60, "S")
    (base / "stat").unlink()
    (base / "stat").mkdir()  # cannot be opened; the owner is then unknown, not gone
    assert _pids(tmp_path) == [60]


def test_an_unreadable_thread_stat_does_not_hide_the_process(tmp_path):
    base = _proc(tmp_path, 61, "Z", tasks={61: "Z", 62: "Z"})
    unreadable = base / "task" / "62" / "stat"
    unreadable.unlink()
    unreadable.mkdir()  # opening it fails whoever runs the test
    assert _pids(tmp_path) == [61]


def test_an_exited_process_state_x_is_not_taken_for_a_zombie(tmp_path):
    _proc(tmp_path, 70, "X", tasks={70: "X"})
    assert _pids(tmp_path) == [70]


def _task_is_a_file(base):
    (base / "task").write_text("not a directory")


def _task_is_empty(base):
    (base / "task").mkdir()


def _task_is_closed(base):
    (base / "task").mkdir()
    os.chmod(base / "task", 0)


@pytest.mark.parametrize(
    "shape",
    [_task_is_a_file, _task_is_empty, _task_is_closed],
    ids=["task-is-a-file", "task-is-empty", "task-is-mode-000"],
)
def test_a_task_list_that_cannot_be_read_does_not_hide_the_process(tmp_path, shape):
    """Its own stat says Z but the threads cannot be listed: it may be alive, so it stays."""
    if shape is _task_is_closed and os.getuid() == 0:
        pytest.skip("root lists any directory")
    base = _proc(tmp_path, 80, "Z")
    base_environ = base / "environ"
    base_environ.unlink()
    base_environ.mkdir()  # cannot be read, as for a process that hides its environment
    shape(base)
    try:
        assert _pids(tmp_path) == [80]
    finally:
        if (base / "task").is_dir():
            os.chmod(base / "task", 0o700)


def test_a_process_whose_own_stat_is_not_z_is_alive_without_reading_its_tasks(tmp_path):
    base = _proc(tmp_path, 81, "S", tasks={81: "Z"})  # inconsistent: the main thread decides
    assert _pids(tmp_path) == [81]
    assert base.exists()


def _task_listings(monkeypatch, *listings):
    """os.listdir of a .../task path answers from `listings` in turn (the last one repeats)."""
    real = os.listdir
    queue = list(listings)

    def listdir(path):
        if str(path).endswith("/task"):
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return item
        return real(path)

    monkeypatch.setattr(os, "listdir", listdir)


def test_a_thread_made_between_the_two_task_listings_keeps_the_process(tmp_path, monkeypatch):
    """A live thread can start a new one and end before its state is read: the list changed."""
    _proc(tmp_path, 90, "Z", tasks={90: "Z", 91: "Z"})
    _task_listings(monkeypatch, ["90", "91"], ["90", "91", "92"])
    assert _pids(tmp_path) == [90]


def test_two_equal_task_listings_with_every_thread_zombie_leave_the_process_out(
    tmp_path, monkeypatch
):
    _proc(tmp_path, 93, "Z", tasks={93: "Z", 94: "Z"})
    _task_listings(monkeypatch, ["93", "94"], ["94", "93"])
    assert _pids(tmp_path) == []


def test_a_task_directory_gone_at_the_second_listing_counts_as_gone(tmp_path, monkeypatch):
    _proc(tmp_path, 95, "Z", tasks={95: "Z"})
    _task_listings(monkeypatch, ["95"], FileNotFoundError())
    assert _pids(tmp_path) == []


def test_a_task_directory_that_cannot_be_listed_the_second_time_keeps_the_process(
    tmp_path, monkeypatch
):
    _proc(tmp_path, 96, "Z", tasks={96: "Z"})
    _task_listings(monkeypatch, ["96"], PermissionError())
    assert _pids(tmp_path) == [96]
