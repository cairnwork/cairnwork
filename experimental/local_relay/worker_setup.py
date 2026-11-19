"""Prepare the isolated run layout required by local-worker-tool."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
from pathlib import Path

from experimental.local_worker.canary import INSTALLED_PYROOT


def prepare_worker_layout(run_root: Path, checkout: Path) -> None:
    """Create canary-compatible scratch, pytest runtime, and worker-writable checkout modes."""
    for name in ("tmp", "q"):
        path = run_root / name
        path.mkdir(mode=0o770)
        os.chmod(path, 0o770)
    _set_checkout_modes(checkout)
    python = _worker_python()
    subprocess.run(["uv", "venv", "-q", "--python", python, str(run_root / "venv")], check=True)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            str(run_root / "venv" / "bin" / "python"),
            "pytest",
        ],
        check=True,
    )


def _worker_python() -> str:
    matches = sorted(Path(INSTALLED_PYROOT).resolve().glob("cpython-3.13*/bin/python3.13"))
    if not matches:
        raise RuntimeError(f"no cpython-3.13 under {INSTALLED_PYROOT}")
    return str(matches[-1])


def _set_checkout_modes(checkout: Path) -> None:
    """Grant the worker group checkout access without following symlinks."""
    for _root, _directories, files, root_fd in os.fwalk(checkout, follow_symlinks=False):
        os.fchmod(root_fd, 0o770)
        for name in files:
            _set_file_mode(name, root_fd)


def _set_file_mode(name: str, root_fd: int) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return
        raise
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            os.fchmod(fd, 0o660)
    finally:
        os.close(fd)
