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
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise RuntimeError(f"refusing hard-linked checkout file: {name}")
            os.fchown(fd, -1, gid)
            os.fchmod(fd, 0o660)
    finally:
        os.close(fd)


_GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def executable_paths(repo: Path, base_sha: str) -> frozenset[bytes]:
    """Executable-bit paths of the base commit, read from the TRUSTED source repo before the worker
    runs (never from the worker-writable checkout), with hooks and fsmonitor off."""
    listing = subprocess.run(
        ["git", *_GIT_SAFE, "-C", str(repo), "ls-tree", "-r", "-z", base_sha],
        capture_output=True,
        check=True,
        env=hardened_git_env(),
    ).stdout
    return frozenset(
        entry.split(b"\t", 1)[1] for entry in listing.split(b"\0") if entry.startswith(b"100755 ")
    )


def restore_judge_modes(checkout: Path, executable: frozenset[bytes]) -> None:
    """Undo the worker-group modes once the worker has exited, before Core judges the checkout.

    Core requires directories 0755 and files 0644/0755 (0755 only where the base commit has the
    executable bit), and its tree digest binds the group to the relay process's own gid. The whole
    tree, ``.git`` included, loses its worker-group write access. Fails closed, never silently: an
    entry the relay cannot re-own (e.g. created under the worker's uid), a hard-linked file
    (``st_nlink != 1``) or an entry on another device raises RuntimeError.
    """
    gid = os.getegid()
    device = checkout.stat().st_dev
    git_dir = checkout / ".git"
    try:
        for root, _directories, files, root_fd in os.fwalk(checkout, follow_symlinks=False):
            if os.fstat(root_fd).st_dev != device:
                raise RuntimeError(f"cannot restore judge modes (other device): {root}")
            os.fchown(root_fd, -1, gid)
            os.fchmod(root_fd, 0o755)
            in_git = Path(root) == git_dir or git_dir in Path(root).parents
            for name in files:
                _restore_file(
                    name, root_fd, gid, device, Path(root) / name, checkout, executable, in_git
                )
    except PermissionError as exc:
        raise RuntimeError(
            f"cannot restore judge modes (entry not owned by the relay): {exc}"
        ) from exc


def _restore_file(name, root_fd, gid, device, path, checkout, executable, in_git) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return
        if info.st_nlink != 1 or info.st_dev != device:
            raise RuntimeError(f"cannot restore judge modes (hard link or other device): {path}")
        os.fchown(fd, -1, gid)
        if in_git:
            os.fchmod(fd, 0o755 if info.st_mode & 0o100 else 0o644)
        else:
            rel = os.fsencode(path.relative_to(checkout).as_posix())
            os.fchmod(fd, 0o755 if rel in executable else 0o644)
    finally:
        os.close(fd)
