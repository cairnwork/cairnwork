"""One honest #1353 witness run per pytest session, shared by every xdist worker (#1492).

The session-scoped ``broker_report`` fixture is a ~5-8 minute sealed run. Under ``-n auto`` each
worker that touches a test needing it rebuilt it. Here the first worker to ask builds it under an
exclusive file lock and stores the JSON in the pytest base temp dir; the others read that file.
Nothing is asserted here: every test still asserts on the same rows as before, and every test
that proves a tamper changes rows still runs its own ``consume.launch``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

import pra1353_consume as consume
from test_1039_1353_witnesses import PINS


def _build() -> dict:
    return consume.launch(
        PINS, source_repo=Path(os.environ.get("PRA1353_SOURCE_REPO", consume.ROOT))
    )


def _read(data: Path) -> dict | None:
    """The stored report if its first line (a sha256 of the rest) matches, else None."""
    try:
        head, _, body = data.read_bytes().partition(b"\n")
        if head.decode() != hashlib.sha256(body).hexdigest():
            return None
        report = json.loads(body)
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) and isinstance(report.get("rows"), list) else None


def honest_report(tmp_path_factory) -> dict:
    """The honest report; built once per session across xdist workers, else built in-process.

    The file is named for this xdist run's own random id (``PYTEST_XDIST_TESTRUNUID``), so a reused
    ``--basetemp`` cannot leave a stale one and nobody can guess the path; it is opened without
    following links, mode 0600, and a reader refuses anything whose hash line does not match.
    JSON turns tuples into lists, for the builder too, so every worker sees the same value.
    """
    run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID")
    if not os.environ.get("PYTEST_XDIST_WORKER") or not run_id:
        return _build()
    shared = tmp_path_factory.getbasetemp().parent  # the xdist-wide base temp dir
    data, lock = shared / f"pra1353_honest_{run_id}.json", shared / f"pra1353_honest_{run_id}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # released when the descriptor closes
        if (report := _read(data)) is not None:
            return report
        body = json.dumps(_build()).encode()
        tmp = data.with_suffix(".tmp")
        tmp_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | flags & os.O_NOFOLLOW, 0o600)
        with os.fdopen(tmp_fd, "wb") as out:
            out.write(hashlib.sha256(body).hexdigest().encode() + b"\n" + body)
        tmp.replace(data)
        return json.loads(body)
    finally:
        os.close(fd)
