"""#1124: stale ``cairnwork-deny.*`` directories are swept when a recording starts; every safety
rule of the sweep has a test that a one-line mutant of that rule makes fail. Recording tests that
do not patch the temp dir sweep the real $TMPDIR, which is safe because only same-uid, dead-owner,
>1 h cairnwork-deny.* entries qualify."""

import os
import threading
import time

import pytest

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


def test_sweep_uses_one_hour_staleness_boundary(tmp_path):
    recent = tmp_path / "cairnwork-deny.under-hour"
    _make_stale(recent, pid=_dead_pid())
    old_3500 = 100_000.0
    for path in (recent / "owner.pid", recent):
        os.utime(path, (old_3500, old_3500))
    expired = tmp_path / "cairnwork-deny.over-hour"
    _make_stale(expired, pid=_dead_pid())
    old_3700 = 99_800.0
    for path in (expired / "owner.pid", expired):
        os.utime(path, (old_3700, old_3700))

    assert deny_sweep.sweep_stale(tmp_path, now=103_500.0) == 1
    assert recent.exists()
    assert not expired.exists()


def test_sweep_does_not_treat_pid_zero_as_live(tmp_path):
    stale = tmp_path / "cairnwork-deny.pidzero"
    _make_stale(stale, pid=0)
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 1
    assert not stale.exists()


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


def test_sweep_keeps_stale_directory_when_pid_is_inaccessible(tmp_path, monkeypatch):
    if os.getuid() == 0:
        pytest.skip("root can signal every process")
    stale = tmp_path / "cairnwork-deny.inaccessible"
    stale.mkdir()
    pid_file = stale / "owner.pid"
    pid_file.write_text("1\n")
    old = time.time() - 7200
    os.utime(pid_file, (old, old))
    os.utime(stale, (old, old))

    def deny_signal(pid, sig):
        raise PermissionError

    monkeypatch.setattr(deny_sweep.os, "kill", deny_signal)
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 0
    assert stale.exists()


def test_sweep_does_not_open_fifo_owner_pid(tmp_path):
    stale = tmp_path / "cairnwork-deny.fifo"
    stale.mkdir()
    owner_pid = stale / "owner.pid"
    os.mkfifo(owner_pid)
    old = time.time() - 7200
    os.utime(owner_pid, (old, old))
    os.utime(stale, (old, old))
    finished = threading.Event()

    def sweep():
        deny_sweep.sweep_stale(tmp_path, time.time())
        finished.set()

    worker = threading.Thread(target=sweep, daemon=True)
    worker.start()
    assert finished.wait(5), "sweep blocked while opening FIFO owner.pid"


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
    old = time.time() - 7200
    os.utime(d / "link", (old, old), follow_symlinks=False)
    os.utime(d, (old, old))
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


def _aged(path, mtime, pid=None):
    path.mkdir()
    if pid is not None:
        (path / "owner.pid").write_text(f"{pid}\n")
        os.utime(path / "owner.pid", (mtime, mtime))
    os.utime(path, (mtime, mtime))


def test_the_default_limit_removes_fifty_and_leaves_the_rest(tmp_path):
    assert deny_sweep.SWEEP_MAX_REMOVE == 50
    for i in range(52):
        _make_stale(tmp_path / f"cairnwork-deny.n{i}")
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 50
    assert len(list(tmp_path.glob("cairnwork-deny.*"))) == 2


@pytest.mark.parametrize("age, removed", [(3600.0, 0), (3600.01, 1)])
def test_a_directory_exactly_one_hour_old_is_kept(tmp_path, age, removed):
    now = 200_000.0
    _aged(tmp_path / "cairnwork-deny.edge", now - age, pid=_dead_pid())
    assert deny_sweep.sweep_stale(tmp_path, now) == removed
    assert (tmp_path / "cairnwork-deny.edge").exists() == (removed == 0)


@pytest.mark.parametrize("error, live", [(OverflowError, False), (OSError, True)])
def test_an_owner_whose_state_is_unknown_is_kept(monkeypatch, error, live):
    """Flipped on purpose (#995): an OSError other than "no such process" used to read as not live,
    so the sweep removed a directory whose owner it could not see."""

    def fail(pid, sig):
        raise error

    monkeypatch.setattr(deny_sweep.os, "kill", fail)
    assert deny_sweep._is_live_pid(5) is live


def test_an_owner_pid_that_is_not_a_number_does_not_protect_the_directory(tmp_path):
    stale = tmp_path / "cairnwork-deny.garbage"
    _aged(stale, time.time() - 7200, pid="not-a-number")
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 1
    assert not stale.exists()


def test_a_plain_file_with_the_prefix_is_left_alone(tmp_path):
    plain = tmp_path / "cairnwork-deny.file"
    plain.write_text("keep")
    old = time.time() - 7200
    os.utime(plain, (old, old))
    assert deny_sweep.sweep_stale(tmp_path, time.time()) == 0
    assert plain.read_text() == "keep"


def test_a_directory_that_will_not_go_is_not_counted_and_tries_stay_bounded(monkeypatch, tmp_path):
    for i in range(5):
        _make_stale(tmp_path / f"cairnwork-deny.stuck{i}")
    tried = []
    monkeypatch.setattr(deny_sweep.shutil, "rmtree", lambda path, **kw: tried.append(path))
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=2) == 0
    assert len(tried) == 2  # the limit counts tries
    assert len(list(tmp_path.glob("cairnwork-deny.*"))) == 5


def test_a_removal_that_raises_still_counts_as_a_try(monkeypatch, tmp_path):
    for i in range(5):
        _make_stale(tmp_path / f"cairnwork-deny.boom{i}")
    tried = []

    def boom(path, **kw):
        tried.append(path)
        raise OSError("busy")

    monkeypatch.setattr(deny_sweep.shutil, "rmtree", boom)
    assert deny_sweep.sweep_stale(tmp_path, time.time(), max_remove=2) == 0
    assert len(tried) == 2
