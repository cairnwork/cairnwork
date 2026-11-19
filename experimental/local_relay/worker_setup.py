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
    gid = run_root.parent.stat().st_gid  # the runs directory's group (setgid, worker group)
    for name in ("tmp", "q"):
        path = run_root / name
        path.mkdir(mode=0o770)
        os.chmod(path, 0o770)
        os.chown(path, -1, gid)
    _set_checkout_modes(checkout, gid)
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
    _regroup_tree(run_root / "venv", gid)


def _worker_python() -> str:
    matches = sorted(Path(INSTALLED_PYROOT).resolve().glob("cpython-3.13*/bin/python3.13"))
    if not matches:
        raise RuntimeError(f"no cpython-3.13 under {INSTALLED_PYROOT}")
    return str(matches[-1])


def _set_checkout_modes(checkout: Path, gid: int) -> None:
    """Hand the checkout to the worker group (dirs 0770, files 0660) without following symlinks."""
    for _root, _directories, files, root_fd in os.fwalk(checkout, follow_symlinks=False):
        os.fchown(root_fd, -1, gid)
        os.fchmod(root_fd, 0o770)
        for name in files:
            _set_file_mode(name, root_fd, gid)


def _regroup_tree(tree: Path, gid: int) -> None:
    """Set only the group on an existing tree (symlinks untouched)."""
    if not tree.is_dir():
        return
    for _root, _directories, files, root_fd in os.fwalk(tree, follow_symlinks=False):
        os.fchown(root_fd, -1, gid)
        for name in files:
            try:
                os.chown(name, -1, gid, dir_fd=root_fd, follow_symlinks=False)
            except OSError as exc:
                if exc.errno != errno.EPERM:
                    raise


def _set_file_mode(name: str, root_fd: int, gid: int) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return
        raise
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            os.fchown(fd, -1, gid)
            os.fchmod(fd, 0o660)
    finally:
        os.close(fd)
