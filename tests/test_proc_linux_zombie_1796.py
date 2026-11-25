"""#1796: a zombie runs nothing, so the Linux process table leaves it out, as the macOS one does.
Its environ cannot be opened by a user who is not root, which made a killed, unreaped child look
like a hidden live process and kept the attempt."""

from cairnwork.proc_linux import _linux_processes


def _proc(root, pid, state, environ):
    base = root / str(pid)
    base.mkdir()
    (base / "status").write_text("Name:\tx\nUid:\t1000\t1000\t1000\t1000\n")
    (base / "stat").write_text(f"{pid} (x) {state} 1 {pid} {pid} 0 -1\n")
    (base / "environ").write_bytes(environ)


def test_a_zombie_is_left_out_of_the_table(tmp_path):
    _proc(tmp_path, 10, "Z", b"A=1\0")
    _proc(tmp_path, 11, "S", b"A=1\0")
    assert [row[0] for row in _linux_processes(str(tmp_path))] == [11]


def test_a_sleeping_process_stays_in_the_table(tmp_path):
    _proc(tmp_path, 12, "S", b"A=1\0")
    assert [row[0] for row in _linux_processes(str(tmp_path))] == [12]
