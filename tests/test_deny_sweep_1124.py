"""#1124: stale ``cairnwork-deny.*`` directories are swept when a recording starts; every safety
rule of the sweep has a test that a one-line mutant of that rule makes fail."""

import os
import time

from cairnwork import deny_channel, deny_sweep

TAG = "cwvtag-0123456789abcdef"


def _dead_pid() -> int:
    pid = 999999
    while True:
        try:
            os.kill(pid, 0)
            pid += 1
        except ProcessLookupError:
            return pid
        except OSError:
            pid += 1


def test_sweep_removes_stale_directory_with_dead_pid(tmp_path):
    stale = tmp_path / "cairnwork-deny.dead"
    stale.mkdir()
    pid_file = stale / "owner.pid"
    pid_file.write_text(f"{_dead_pid()}\n")
    old = time.time() - 7200
    os.utime(pid_file, (old, old))
    os.utime(stale, (old, old))
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 1
    assert not stale.exists()


def test_sweep_keeps_fresh_directory(tmp_path):
    fresh = tmp_path / "cairnwork-deny.fresh"
    fresh.mkdir()
    pid_file = fresh / "owner.pid"
    pid_file.write_text(f"{_dead_pid()}\n")
    recent = time.time() - 300
    os.utime(pid_file, (recent, recent))
    os.utime(fresh, (recent, recent))
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 0
    assert fresh.exists()


def test_sweep_keeps_stale_directory_with_live_owner_pid(tmp_path):
    alive = tmp_path / "cairnwork-deny.alive"
    alive.mkdir()
    pid_file = alive / "owner.pid"
    pid_file.write_text(f"{os.getpid()}\n")
    old = time.time() - 7200
    os.utime(pid_file, (old, old))
    os.utime(alive, (old, old))
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 0
    assert alive.exists()


def test_sweep_keeps_symlink_and_untouched_target(tmp_path):
    target = tmp_path / "target_dir"
    target.mkdir()
    marker = target / "file.txt"
    marker.write_text("keep")
    link = tmp_path / "cairnwork-deny.link"
    link.symlink_to(target, target_is_directory=True)
    old = time.time() - 7200
    os.utime(marker, (old, old))
    os.utime(target, (old, old))
    os.utime(link, (old, old), follow_symlinks=False)  # stale by age: only rule 2 keeps it
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 0
    assert link.is_symlink()
    assert target.exists()
    assert marker.read_text() == "keep"


def test_sweep_keeps_unrelated_stale_directory(tmp_path):
    other = tmp_path / "other-prefix.x"
    other.mkdir()
    old = time.time() - 7200
    os.utime(other, (old, old))
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 0
    assert other.exists()


def test_sweep_removes_stale_directory_without_pid_file(tmp_path):
    stale = tmp_path / "cairnwork-deny.nopid"
    stale.mkdir()
    old = time.time() - 7200
    os.utime(stale, (old, old))
    removed = deny_sweep.sweep_stale(tmp_path, time.time())
    assert removed == 1
    assert not stale.exists()


def test_recording_writes_owner_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(deny_channel.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    with deny_channel.recording(TAG, lambda argv, log: ""):
        (made,) = tmp_path.glob("cairnwork-deny.*")
        assert (made / "owner.pid").read_text().strip() == str(os.getpid())


def test_recording_invokes_sweep_stale(monkeypatch):
    called = []
    monkeypatch.setattr(deny_channel, "_sweep_stale", lambda *a, **k: called.append(True))
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    with deny_channel.recording(TAG, lambda argv, log: ""):
        pass
    assert called == [True]


def test_recording_start_removes_stale_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(deny_channel.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    stale = tmp_path / "cairnwork-deny.oldrun"
    stale.mkdir()
    pid_file = stale / "owner.pid"
    pid_file.write_text(f"{_dead_pid()}\n")
    old = time.time() - 7200
    os.utime(pid_file, (old, old))
    os.utime(stale, (old, old))

    with deny_channel.recording(TAG, lambda argv, log: ""):
        assert not stale.exists()


def _make_stale(path, *, pid=None):
    path.mkdir()
    old = time.time() - 7200
    if pid is not None:
        (path / "owner.pid").write_text(f"{pid}\n")
        os.utime(path / "owner.pid", (old, old))
    os.utime(path, (old, old))


def test_sweep_keeps_a_directory_owned_by_another_uid(monkeypatch, tmp_path):
    _make_stale(tmp_path / "cairnwork-deny.other", pid=_dead_pid())
    other_uid = os.getuid() + 1
    monkeypatch.setattr(deny_sweep.os, "getuid", lambda: other_uid)
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 0
    assert (tmp_path / "cairnwork-deny.other").exists()


def test_sweep_keeps_a_stale_directory_whose_stream_is_still_written(tmp_path):
    d = tmp_path / "cairnwork-deny.busy"
    _make_stale(d, pid=_dead_pid())
    (d / "stream.ndjson").write_text("{}\n")  # fresh child mtime
    os.utime(d, (time.time() - 7200, time.time() - 7200))
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 0
    assert (d / "stream.ndjson").exists()


def test_sweep_does_not_follow_a_symlink_inside_a_stale_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    d = tmp_path / "cairnwork-deny.withlink"
    _make_stale(d, pid=_dead_pid())
    (d / "link").symlink_to(outside, target_is_directory=True)
    os.utime(d / "link", (time.time() - 7200,) * 2, follow_symlinks=False)
    os.utime(d, (time.time() - 7200, time.time() - 7200))
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 1
    assert not d.exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_sweep_removes_at_most_max_remove_per_start(tmp_path):
    for i in range(3):
        _make_stale(tmp_path / f"cairnwork-deny.n{i}")
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=2) == 2
    assert len(list(tmp_path.glob("cairnwork-deny.*"))) == 1


def test_a_failing_sweep_does_not_stop_the_recording(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("sweep broke")

    monkeypatch.setattr(deny_channel, "_sweep_stale", boom)
    monkeypatch.setattr(deny_channel, "_start", lambda path, window: None)
    with deny_channel.recording(TAG, lambda argv, log: "") as box:
        assert box is not None
