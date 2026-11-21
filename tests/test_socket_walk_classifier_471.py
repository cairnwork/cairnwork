"""#471 step 1: the socket-walk script's location classes (pure, no sandbox)."""

import importlib.util
from pathlib import Path

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

    def fake_walk(top, followlinks=False):
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
