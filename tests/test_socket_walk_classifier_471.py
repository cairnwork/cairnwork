"""#471 step 1: the socket-walk script's location classes (pure, no sandbox)."""

import importlib.util
import os
import socket
import tempfile
from pathlib import Path

import pytest

_path = Path(__file__).parents[1] / "scripts" / "socket_walk_471.py"
_spec = importlib.util.spec_from_file_location("socket_walk_471", _path)
assert _spec is not None and _spec.loader is not None
walk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(walk)


def test_location_classes():
    cases = {
        "/tmp/x": "private-trees",
        "/run/x": "private-trees",
        "/var/run/x": "private-trees",
        "/var/tmp/x": "var-tmp",
        "/var/lib/x": "var-lib",
        "/home/a/x": "home",
        "/srv/x": "srv",
        "/opt/x": "opt",
        "/usr/lib/x": "usr-and-other-system",
        "/weird/x": "other",
    }
    assert {path: walk.location(path) for path in cases} == cases


def test_walk_reports_truncated_when_budget_expires_between_files(monkeypatch):
    seen = []

    def fake_walk(top, followlinks=False, **_kw):
        for index in range(1000):
            seen.append(index)
            yield f"/d{index}", [], []  # directories only: no file is ever processed

    clock = iter([0.0] + [10**6] * 5000)
    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.time, "monotonic", lambda: next(clock))
    report = walk.collect()
    assert report["truncated|-|-"] == 1 and report["stopped-by-time|-|-"] == 1
    assert len(seen) < 5


def test_unwritable_summary_path_is_not_an_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(walk, "collect", lambda: {"home|stream|refused": 2})
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing-dir" / "summary.md"))
    walk.main()
    assert capsys.readouterr().out.strip() == '{"home|stream|refused": 2}'


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list any directory")
def test_unlistable_directory_is_counted_not_silent():
    base = Path(tempfile.mkdtemp(prefix="cw471u-", dir="/tmp")).resolve()
    hidden = base / "d"
    hidden.mkdir()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(hidden / "s.sock"))
    listener.listen()
    hidden.chmod(0o311)  # enterable but not listable
    try:
        report = walk.collect(str(base))
    finally:
        hidden.chmod(0o700)
        listener.close()
        (hidden / "s.sock").unlink()
        hidden.rmdir()
        base.rmdir()
    assert sum(n for key, n in report.items() if key.endswith("|-|unlistable")) == 1


def test_overlong_path_is_probed_by_relative_name_and_reported_apart():
    base = Path(tempfile.mkdtemp(prefix="cw471l-", dir="/tmp")).resolve()
    deep = base
    for _ in range(4):
        deep = deep / ("d" * 60)
        deep.mkdir()
    sock_path = deep / "s.sock"
    assert len(str(sock_path)) > 200
    old = os.getcwd()
    listener = socket.socket(socket.AF_UNIX)
    try:
        os.chdir(deep)
        listener.bind("s.sock")
        listener.listen()
        os.chdir(old)
        assert walk._probe(str(sock_path)) == ("stream", "pathtoolong_connectable")
    finally:
        os.chdir(old)
        listener.close()
        sock_path.unlink()
        for d in (deep, *deep.parents):
            if d == base.parent:
                break
            d.rmdir()


def _long_missing_path() -> str:
    return "/tmp/" + "/".join(["m" * 60] * 4) + "/s.sock"


def test_probe_restores_cwd_on_success_and_on_failure():
    here = os.getcwd()
    assert walk._probe(_long_missing_path()) == ("stream", "pathtoolong_other")
    assert os.getcwd() == here
    base = Path(tempfile.mkdtemp(prefix="cw471r-", dir="/tmp")).resolve()
    deep = base
    for _ in range(4):
        deep = deep / ("d" * 60)
        deep.mkdir()
    listener = socket.socket(socket.AF_UNIX)
    try:
        os.chdir(deep)
        listener.bind("s.sock")
        listener.listen()
        os.chdir(here)
        assert walk._probe(str(deep / "s.sock"))[1] == "pathtoolong_connectable"
        assert os.getcwd() == here
    finally:
        os.chdir(here)
        listener.close()
        (deep / "s.sock").unlink()
        for d in (deep, *deep.parents):
            if d == base.parent:
                break
            d.rmdir()


def test_probe_with_unreadable_or_deleted_cwd_reports_pathtoolong_other():
    here = os.getcwd()
    gone = Path(tempfile.mkdtemp(prefix="cw471g-", dir="/tmp"))
    try:
        os.chdir(gone)
        gone.rmdir()
        assert walk._probe(_long_missing_path()) == ("stream", "pathtoolong_other")
    finally:
        os.chdir(here)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can open any directory")
def test_probe_with_unreadable_cwd_reports_pathtoolong_other():
    here = os.getcwd()
    x_only = Path(tempfile.mkdtemp(prefix="cw471x-", dir="/tmp"))
    try:
        os.chdir(x_only)
        x_only.chmod(0o311)
        assert walk._probe(_long_missing_path()) == ("stream", "pathtoolong_other")
    finally:
        os.chdir(here)
        x_only.chmod(0o700)
        x_only.rmdir()


def test_probe_marks_walk_lost_when_cwd_cannot_be_restored(monkeypatch):
    here = os.getcwd()

    def failing_fchdir(_fd):
        raise OSError(13, "denied")

    monkeypatch.setattr(walk.os, "fchdir", failing_fchdir)
    monkeypatch.setattr(walk, "_cwd_lost", False)
    try:
        assert walk._probe(_long_missing_path()) == ("stream", "pathtoolong_other")
        assert walk._cwd_lost is True
    finally:
        os.chdir(here)


def test_collect_stops_truncated_when_cwd_lost(monkeypatch):
    import stat
    import types

    probed = []

    def fake_probe(path):
        probed.append(path)
        monkeypatch.setattr(walk, "_cwd_lost", True)
        return "stream", "pathtoolong_other"

    def fake_walk(top, followlinks=False, **_kw):
        yield "/d", [], ["a.sock", "b.sock"]

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", lambda _p: types.SimpleNamespace(st_mode=stat.S_IFSOCK))
    monkeypatch.setattr(walk, "_probe", fake_probe)
    monkeypatch.setattr(walk, "_cwd_lost", False)
    report = walk.collect()
    assert report["truncated|-|-"] == 1 and report["stopped-by-cwd|-|-"] == 1
    assert len(probed) == 1  # the second socket is not probed from the wrong directory


def test_big_system_trees_are_pruned_not_walked(monkeypatch):
    seen = []

    def fake_walk(top, followlinks=False, **_kw):
        dirs = ["usr", "tmp", "lib64", "var", "bin", "sbin", "lib", "lib32"]
        yield "/", dirs, []
        seen.extend(dirs)

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    walk.collect()
    assert seen == ["tmp", "var"]


def test_entry_cap_stops_with_its_own_reason(monkeypatch):
    monkeypatch.setattr(walk, "MAX_ENTRIES", 2)

    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], ["a", "b", "c"]

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", lambda _p: (_ for _ in ()).throw(OSError()))
    report = walk.collect()
    assert report["stopped-by-entries|-|-"] == 1 and report["walked-private-trees|-|-"] == 3


def _blocking_lstat(release, ok_name=None):
    """An lstat that never returns until released: a hung mount, for the walk's hard deadline."""
    import stat as stat_mod
    import types

    real = os.lstat

    def lstat(path):
        if ok_name and str(path).endswith(ok_name):
            return types.SimpleNamespace(st_mode=stat_mod.S_IFSOCK)
        if not str(path).endswith("hang"):
            return real(path)  # realpath() and friends still work
        release.wait(60)
        raise OSError("released")

    return lstat


def test_one_blocked_call_cannot_hold_the_report_past_the_hard_deadline(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], ["ok", "hang"]

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", _blocking_lstat(release, "ok"))
    monkeypatch.setattr(walk, "_probe", lambda _p: ("stream", "refused"))
    monkeypatch.setattr(walk, "BUDGET_SECONDS", 1)
    monkeypatch.setattr(walk, "HANG_GRACE_SECONDS", 1)
    started = time.monotonic()
    try:
        report = walk.collect()
    finally:
        release.set()
    assert time.monotonic() - started < 8
    assert report["truncated|-|-"] == 1 and report["stopped-by-hang|-|-"] == 1
    assert report["private-trees|stream|refused"] == 1  # what was measured before the hang stays


def test_a_worker_error_is_raised_not_swallowed(monkeypatch):
    def fake_walk(top, followlinks=False, **_kw):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    with pytest.raises(RuntimeError, match="boom"):
        walk.collect()


def test_script_main_prints_a_hang_report_and_exits_by_the_deadline(tmp_path):
    """Real entry: the script's own main() in a child whose lstat never returns."""
    import json
    import subprocess
    import sys
    import time

    marker = "cw1603-private-marker"
    cwd = tmp_path / marker
    cwd.mkdir()
    summary = tmp_path / "summary.md"
    child = (
        "import importlib.util, threading, os\n"
        f"spec = importlib.util.spec_from_file_location('w', {str(_path)!r})\n"
        "w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)\n"
        "w.BUDGET_SECONDS, w.HANG_GRACE_SECONDS = 1, 1\n"
        "gate = threading.Event()\n"
        "os.walk = lambda *a, **k: iter([('/tmp', [], ['file'])])  # a file is reached at once\n"
        "os.lstat = lambda p: gate.wait(120)\n"
        "w.main()\n"
    )
    started = time.monotonic()
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", child],
        capture_output=True,
        timeout=30,
        check=False,
        cwd=cwd,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert time.monotonic() - started < 5  # the deadline is 2 s plus interpreter start
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    report = json.loads(result.stdout.decode())
    assert report["truncated|-|-"] == 1 and report["stopped-by-hang|-|-"] == 1
    assert "stopped-by-hang" in summary.read_text()  # the summary is written before the exit
    assert marker not in result.stdout.decode() + result.stderr.decode() + summary.read_text()


def test_main_exits_at_once_after_a_hang_report(monkeypatch, tmp_path):
    exits = []
    summary = tmp_path / "s.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(walk, "collect", lambda: {"stopped-by-hang|-|-": 1, "truncated|-|-": 1})
    monkeypatch.setattr(walk.os, "_exit", lambda code: exits.append((code, summary.exists())))
    walk.main()
    assert exits == [(0, True)]


def test_main_does_not_hard_exit_on_an_ordinary_report(monkeypatch):
    exits = []
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(walk, "collect", lambda: {"home|stream|refused": 1})
    monkeypatch.setattr(walk.os, "_exit", lambda code: exits.append(code))
    walk.main()
    assert exits == []


def test_a_call_that_returns_inside_the_grace_is_not_a_hang(monkeypatch):
    import time

    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], ["slow", "next"]

    real = os.lstat

    def slow_lstat(path):
        if not str(path).endswith(("slow", "next")):
            return real(path)
        time.sleep(1.5)  # past the 1 s budget, inside the 6 s grace
        raise OSError("gone")

    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", slow_lstat)
    monkeypatch.setattr(walk, "BUDGET_SECONDS", 1)
    monkeypatch.setattr(walk, "HANG_GRACE_SECONDS", 6)
    report = walk.collect()
    assert report["stopped-by-time|-|-"] == 1 and "stopped-by-hang|-|-" not in report


def _scandir_big_first(real):
    """os.scandir that lists the `big` tree first, the order that starves the others."""

    class Listing:
        def __init__(self, path):
            with real(path) as it:
                entries = sorted(it, key=lambda e: e.name != "big")
            self._it = iter(entries)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._it)

        def close(self):
            pass

    return Listing


def _tree_with_big_and_socket(tmp_path):
    big, other = tmp_path / "big", tmp_path / "other"  # tmp_path is short (see the fixture below)
    big.mkdir()
    other.mkdir()
    for index in range(40):
        (big / f"f{index}").write_text("x")
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(other / "s.sock"))
    listener.listen()
    return big, listener


def test_a_deferred_tree_is_walked_last_so_the_cap_cannot_starve_rest(monkeypatch, short_tmp):
    tmp_path = short_tmp
    big, listener = _tree_with_big_and_socket(tmp_path)
    try:
        monkeypatch.setattr(walk, "DEFER", {str(big)})
        monkeypatch.setattr(walk, "PRIORITY_ROOTS", (str(tmp_path),))
        monkeypatch.setattr(walk, "MAX_ENTRIES", 10)
        monkeypatch.setattr(walk.os, "scandir", _scandir_big_first(os.scandir))
        report = walk.collect(str(tmp_path))
    finally:
        listener.close()
    assert report["stopped-by-entries|-|-"] == 1  # the deferred tree really did hit the cap
    assert report[walk.PRIORITY_DONE] == 1
    assert sum(n for k, n in report.items() if k.endswith("|connectable")) == 1  # the socket


def test_priority_mark_is_absent_when_the_cap_hits_before_deferred_trees(monkeypatch, short_tmp):
    tmp_path = short_tmp
    big, listener = _tree_with_big_and_socket(tmp_path)
    try:
        monkeypatch.setattr(walk, "DEFER", set())  # no deferral: the big tree starves the rest
        monkeypatch.setattr(walk, "MAX_ENTRIES", 10)
        monkeypatch.setattr(walk.os, "scandir", _scandir_big_first(os.scandir))
        report = walk.collect(str(tmp_path))
    finally:
        listener.close()
    assert report["stopped-by-entries|-|-"] == 1
    assert walk.PRIORITY_DONE not in report
    assert not any(key.endswith("|connectable") for key in report)


@pytest.fixture
def short_tmp():
    """A short directory: AF_UNIX paths are limited to ~104 bytes; pytest's tmp_path is longer."""
    import shutil

    path = Path(tempfile.mkdtemp(prefix="w", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _fake_walk_with(failures, entered_roots):
    def fake_walk(top, followlinks=False, onerror=lambda _e: None, **_kw):
        for path in failures:
            onerror(OSError(13, "denied", path))
        for root in entered_roots:
            yield root, [], []
        yield "/opt", [], []

    return fake_walk


def _marker_for(monkeypatch, roots, failures, entered_roots):
    monkeypatch.setattr(walk, "PRIORITY_ROOTS", roots)
    monkeypatch.setattr(walk.os, "walk", _fake_walk_with(failures, entered_roots))
    return walk.collect()


def test_marker_is_given_when_every_priority_root_was_entered_cleanly(monkeypatch, short_tmp):
    root = str(short_tmp)
    assert walk.PRIORITY_DONE in _marker_for(monkeypatch, (root,), [], [root])


def test_marker_is_withheld_when_a_priority_tree_was_unlistable(monkeypatch, short_tmp):
    root = str(short_tmp)
    report = _marker_for(monkeypatch, (root,), [root + "/sub"], [root])
    assert walk.PRIORITY_DONE not in report


def test_marker_is_withheld_when_a_priority_root_exists_but_was_not_entered(monkeypatch, short_tmp):
    real = short_tmp / "real"
    real.mkdir()
    link = short_tmp / "link"
    link.symlink_to(real)  # os.walk does not follow it, so it is never entered
    report = _marker_for(monkeypatch, (str(link),), [], [str(short_tmp)])
    assert walk.PRIORITY_DONE not in report


def test_marker_is_withheld_when_a_file_could_not_be_statted(monkeypatch):
    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], ["a"]
        yield "/opt", [], []

    def denied(_path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(walk, "PRIORITY_ROOTS", ("/tmp",))
    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", denied)
    report = walk.collect()
    assert walk.PRIORITY_DONE not in report and report["private-trees|-|unstatable"] == 1


def test_a_vanished_file_does_not_withhold_the_marker(monkeypatch):
    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], ["a"]
        yield "/opt", [], []

    def gone(_path):
        raise FileNotFoundError(2, "gone")

    monkeypatch.setattr(walk, "PRIORITY_ROOTS", ("/tmp",))
    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", gone)
    assert walk.PRIORITY_DONE in walk.collect()
