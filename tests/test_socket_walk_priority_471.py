"""#1603: the socket-walk priority-completion marker and the deferred /opt walk (no sandbox)."""

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


def test_marker_is_withheld_for_a_dangling_symlink_root(monkeypatch, short_tmp):
    link = short_tmp / "dangling"
    link.symlink_to(short_tmp / "nowhere")  # exists as a link, target does not
    report = _marker_for(monkeypatch, (str(link),), [], [str(short_tmp)])
    assert walk.PRIORITY_DONE not in report


def test_marker_is_given_when_a_priority_root_does_not_exist_at_all(monkeypatch, short_tmp):
    report = _marker_for(monkeypatch, (str(short_tmp / "absent"),), [], [str(short_tmp)])
    assert walk.PRIORITY_DONE in report


def test_a_failure_outside_the_priority_trees_keeps_the_marker(monkeypatch, short_tmp):
    root = str(short_tmp)
    report = _marker_for(monkeypatch, (root,), ["/etc/private-dir"], [root])
    assert walk.PRIORITY_DONE in report  # the root-only directory is still counted below
    assert sum(n for key, n in report.items() if key.endswith("|-|unlistable")) == 1


def test_a_failure_beside_a_priority_root_name_does_not_withhold_the_marker(monkeypatch, short_tmp):
    root = str(short_tmp)
    report = _marker_for(monkeypatch, (root,), [root + "-sibling/sub"], [root])
    assert walk.PRIORITY_DONE in report  # a name that merely starts with the root is not inside it


def test_a_stat_failure_outside_the_priority_trees_keeps_the_marker(monkeypatch):
    def fake_walk(top, followlinks=False, **_kw):
        yield "/tmp", [], []
        yield "/etc", [], ["shadow"]
        yield "/opt", [], []

    def denied(_path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(walk, "PRIORITY_ROOTS", ("/tmp",))
    monkeypatch.setattr(walk.os, "walk", fake_walk)
    monkeypatch.setattr(walk.os, "lstat", denied)
    report = walk.collect()
    assert walk.PRIORITY_DONE in report and report["usr-and-other-system|-|unstatable"] == 1


def test_a_failure_on_a_directory_above_a_priority_root_withholds_the_marker(
    monkeypatch, short_tmp
):
    root = str(short_tmp / "a" / "root")
    report = _marker_for(monkeypatch, (root,), [str(short_tmp / "a")], [str(short_tmp)])
    assert walk.PRIORITY_DONE not in report


def test_a_failure_under_the_resolved_form_of_a_symlinked_root_withholds_the_marker(
    monkeypatch, short_tmp
):
    real = short_tmp / "real"
    real.mkdir()
    link = short_tmp / "link"
    link.symlink_to(real)
    report = _marker_for(
        monkeypatch, (str(link),), [str(real.resolve() / "sub")], [str(real.resolve())]
    )
    assert walk.PRIORITY_DONE not in report


def test_a_root_whose_lstat_is_denied_is_not_treated_as_absent(monkeypatch, short_tmp):
    root = str(short_tmp / "hidden")
    real_lstat = os.lstat

    def denied(path, *a, **k):
        if str(path) == root:
            raise PermissionError(13, "denied")
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(walk.os, "lstat", denied)
    report = _marker_for(monkeypatch, (root,), [], [str(short_tmp)])
    assert walk.PRIORITY_DONE not in report
