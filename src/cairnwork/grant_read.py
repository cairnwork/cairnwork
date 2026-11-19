"""Handle-based read-permission walk for ``VerifierAccount.grant_read`` (#91, #279, #1361)."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_REG_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_O_PATH = getattr(os, "O_PATH", 0)
_LINK_CHMOD = os.chmod in os.supports_follow_symlinks and os.chmod in os.supports_dir_fd


def _ident(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _matches_kind(mode: int, *, is_dir: bool) -> bool:
    return stat.S_ISDIR(mode) if is_dir else stat.S_ISREG(mode)


def _open_dir(root_fd: int, parts: tuple[str, ...]) -> int:
    fd = os.dup(root_fd)
    try:
        for name in parts:
            inner = os.open(name, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = inner
    except BaseException:
        os.close(fd)
        raise
    return fd


def _chmod_unreadable(
    dir_fd: int, name: str, info: os.stat_result, bits: int, *, is_dir: bool
) -> None:
    if _O_PATH:
        flags = _O_PATH | os.O_NOFOLLOW | (os.O_DIRECTORY if is_dir else 0)
        handle = os.open(name, flags, dir_fd=dir_fd)
        try:
            got = os.fstat(handle)
            if not _matches_kind(got.st_mode, is_dir=is_dir) or _ident(got) != _ident(info):
                raise OSError(errno.ESTALE, "replaced while granting read", name)
            os.chmod(f"/proc/self/fd/{handle}", stat.S_IMODE(got.st_mode) | bits)
        finally:
            os.close(handle)
        return
    if not _LINK_CHMOD:
        raise OSError(errno.EOPNOTSUPP, "cannot chmod without following symlinks", name)
    cur = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not _matches_kind(cur.st_mode, is_dir=is_dir) or _ident(cur) != _ident(info):
        raise OSError(errno.ESTALE, "replaced while granting read", name)
    owner = 0o500 if is_dir else 0o400
    os.chmod(name, stat.S_IMODE(cur.st_mode) | bits | owner, dir_fd=dir_fd, follow_symlinks=False)
    after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not _matches_kind(after.st_mode, is_dir=is_dir) or _ident(after) != _ident(info):
        raise OSError(errno.ESTALE, "replaced while granting read", name)


def _chmod_entry(dir_fd: int, name: str, info: os.stat_result, bits: int, *, is_dir: bool) -> bool:
    try:
        fd = os.open(name, _DIR_FLAGS if is_dir else _REG_FLAGS, dir_fd=dir_fd)
    except PermissionError:
        _chmod_unreadable(dir_fd, name, info, bits, is_dir=is_dir)
        return is_dir  # widened (owner bits too), so descend and open its contents next
    try:
        got = os.fstat(fd)
        if not _matches_kind(got.st_mode, is_dir=is_dir) or _ident(got) != _ident(info):
            raise OSError(errno.ESTALE, "replaced while granting read", name)
        os.fchmod(fd, stat.S_IMODE(got.st_mode) | bits)
        return True
    finally:
        os.close(fd)


def _chmod_symlink(dir_fd: int, name: str, info: os.stat_result) -> None:
    if not _LINK_CHMOD:
        return
    cur = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISLNK(cur.st_mode) or _ident(cur) != _ident(info):
        raise OSError(errno.ESTALE, "replaced while granting read", name)
    os.chmod(name, stat.S_IMODE(cur.st_mode) | 0o055, dir_fd=dir_fd, follow_symlinks=False)
    after = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISLNK(after.st_mode) or _ident(after) != _ident(info):
        raise OSError(errno.ESTALE, "replaced while granting read", name)


def grant_read(root: Path) -> None:
    """Open read/traverse bits for group/other across ``root`` using O_NOFOLLOW handles."""
    root_fd = os.open(root, _DIR_FLAGS)
    try:
        os.fchmod(root_fd, stat.S_IMODE(os.fstat(root_fd).st_mode) | 0o055)
        stack: list[tuple[str, ...]] = [()]
        while stack:
            parts = stack.pop()
            dir_fd = _open_dir(root_fd, parts)
            try:
                for name in sorted(os.listdir(dir_fd)):
                    info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        _chmod_symlink(dir_fd, name, info)
                    elif stat.S_ISDIR(info.st_mode):
                        if _chmod_entry(dir_fd, name, info, 0o055, is_dir=True):
                            stack.append((*parts, name))
                    elif stat.S_ISREG(info.st_mode):
                        _chmod_entry(dir_fd, name, info, 0o044, is_dir=False)
            finally:
                os.close(dir_fd)
    finally:
        os.close(root_fd)
