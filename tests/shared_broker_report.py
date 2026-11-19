"""One honest #1353 witness run per pytest session, shared by every xdist worker (#1492).

The session-scoped ``broker_report`` fixture is a ~5-8 minute sealed run. Under ``-n auto`` each
worker that touches a test needing it rebuilt it. Here the first worker to ask builds it under an
exclusive file lock and stores the JSON in the pytest base temp dir; the others read that file.
Nothing is asserted here: every test still asserts on the same rows as before, and every test
that proves a tamper changes rows still runs its own ``consume.launch``.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pra1353_consume as consume
from test_1039_1353_witnesses import PINS


def _build() -> dict:
    return consume.launch(
        PINS, source_repo=Path(os.environ.get("PRA1353_SOURCE_REPO", consume.ROOT))
    )


def honest_report(tmp_path_factory) -> dict:
    """The honest report; built once per session across xdist workers, else built in-process."""
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return _build()
    shared = tmp_path_factory.getbasetemp().parent  # the xdist-wide base temp dir
    data, lock = shared / "pra1353_honest_report.json", shared / "pra1353_honest_report.lock"
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # released when the handle closes
        if data.exists():
            return json.loads(data.read_text())
        report = _build()
        tmp = data.with_suffix(".tmp")
        tmp.write_text(json.dumps(report))
        tmp.replace(data)
        # Same value a reader gets, so every worker sees identical types (tuples as lists).
        return json.loads(data.read_text())
