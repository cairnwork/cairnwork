"""Prepare the isolated run layout required by local-worker-tool."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
from pathlib import Path

from experimental.local_worker.canary import INSTALLED_PYROOT
from experimental.pause.git_env import hardened_git_env


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


_GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def restore_judge_modes(checkout: Path, base_sha: str) -> None:
    """Undo the worker-group modes once the worker has exited, before Core judges the checkout.

    Core requires directories 0755 and files 0644/0755 (0755 only where the base commit has the
    executable bit), and its tree digest binds the group to the relay process's own gid. The
    executable set comes from the base commit's tree (not the worker-writable index) and git runs
    with hooks and fsmonitor off. An entry the relay cannot re-own (for example a file the worker
    created under its own uid) fails closed with an explicit error, never a silent success.
    """
    gid = os.getegid()
    listing = subprocess.run(
        ["git", *_GIT_SAFE, "-C", str(checkout), "ls-tree", "-r", "-z", base_sha],
        capture_output=True,
        check=True,
        env=hardened_git_env(),
    ).stdout
    executable = {
        entry.split(b"\t", 1)[1] for entry in listing.split(b"\0") if entry.startswith(b"100755 ")
    }
    try:
        for root, directories, files, root_fd in os.fwalk(checkout, follow_symlinks=False):
            if Path(root) == checkout:
                directories[:] = [name for name in directories if name != ".git"]
            os.fchown(root_fd, -1, gid)
            os.fchmod(root_fd, 0o755)
            for name in files:
                _restore_file(name, root_fd, gid, Path(root) / name, checkout, executable)
    except PermissionError as exc:
        raise RuntimeError(
            f"cannot restore judge modes (entry not owned by the relay): {exc}"
        ) from exc


def _restore_file(
    name: str, root_fd: int, gid: int, path: Path, checkout: Path, executable: set[bytes]
) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return
        raise
    try:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            os.fchown(fd, -1, gid)
            rel = os.fsencode(path.relative_to(checkout).as_posix())
            os.fchmod(fd, 0o755 if rel in executable else 0o644)
    finally:
        os.close(fd)
