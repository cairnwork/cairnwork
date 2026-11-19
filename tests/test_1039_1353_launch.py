"""Fail-closed unit tests for the #1353 route launcher (stand-in children, no product code)."""

from __future__ import annotations

import pytest
from pra1353_launch import launch_route

_REPORT = "import sys; open(sys.argv[2], 'w').write('{\"modules\": []}')\n"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRA1353_CHILD_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("PRA1353_IMPORT_ROOTS", "[]")


def test_one_json_line_and_report_pass():
    boot = _REPORT + 'print(\'{"reason": "ok"}\')'
    assert launch_route("stand-in", {}, boot=boot) == {"reason": "ok"}


@pytest.mark.parametrize(
    ("boot", "message"),
    [
        ("import sys; sys.exit(3)", "exited 3"),
        ("print('{}'); print('{}')", "exactly one JSON line"),
        ("print('nope')", "not JSON"),
        ("print('[1]')", "not a JSON object"),
        ("pass", "exactly one JSON line"),
        ("print('{}')", "module-origin report"),
    ],
)
def test_bad_child_raises_instead_of_producing_a_row(boot, message):
    with pytest.raises(RuntimeError, match=message):
        launch_route("stand-in", {}, boot=boot)


def test_timeout_raises():
    with pytest.raises(RuntimeError, match="timed out"):
        launch_route("stand-in", {}, timeout=1, boot="import time; time.sleep(30)")
