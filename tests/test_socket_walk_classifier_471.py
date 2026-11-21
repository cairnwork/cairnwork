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
    assert walk.collect() == {"truncated|-|-": 1}
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
