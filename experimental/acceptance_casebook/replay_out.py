"""Fail-closed append-only writer for replay output chains."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from experimental.acceptance_casebook.replay_config import metadata as _state_config_metadata
from experimental.acceptance_casebook.store import CasebookError, _head, _read_chain

_state_config_path: Path | None = None
_state_config_runner: Any = None


def configure_state_config(path: Path | None, runner: Any = None) -> Any:
    global _state_config_path, _state_config_runner
    _state_config_path, _state_config_runner = path, runner
    return runner


def _protected(paths: tuple[Path, ...]) -> list[os.stat_result]:
    found = []
    for path in paths:
        try:
            found.append(path.stat())
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc
    return found


def _check(fd: int, paths: tuple[Path, ...]) -> None:
    try:
        opened = os.fstat(fd)
        protected = _protected(paths)
    except OSError as exc:
        raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc
    if not stat.S_ISREG(opened.st_mode) or any(
        (opened.st_dev, opened.st_ino) == (item.st_dev, item.st_ino) for item in protected
    ):
        raise CasebookError("REPLAY_OUTPUT_REFUSED")


def _casefold_collision(output: Path, protected: tuple[Path, ...]) -> bool:
    for path in protected:
        try:
            if (
                output.parent.samefile(path.parent)
                and output.name.casefold() == path.name.casefold()
            ):
                return True
        except OSError:
            return True
    return False


def _paths(out: Path, protected: tuple[Path, ...]) -> tuple[Path, ...]:
    paths = (
        out,
        _head(out),
        out.with_name(out.name + ".head.tmp"),
        out.with_name(out.name + ".lock"),
    )
    if any(_casefold_collision(output, protected) for output in paths):
        raise CasebookError("REPLAY_OUTPUT_REFUSED")
    return paths


def _open(path: Path, flags: int, protected: tuple[Path, ...], exclusive: bool = False) -> int:
    try:
        fd = os.open(
            path, flags | getattr(os, "O_NOFOLLOW", 0) | (os.O_EXCL if exclusive else 0), 0o600
        )
    except OSError as exc:
        raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc
    try:
        _check(fd, protected)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write(
    path: Path, data: bytes, flags: int, protected: tuple[Path, ...], exclusive: bool = False
) -> None:
    fd = _open(path, flags, protected, exclusive)
    try:
        _check(fd, protected)
        with os.fdopen(fd, "ab") as handle:
            handle.write(data)
            handle.flush()
    except OSError as exc:
        raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc


def append(out: Path, record: dict[str, Any], protected: tuple[Path, ...]) -> None:
    record = {**record, **_state_config_metadata(_state_config_path, _state_config_runner)}
    paths = _paths(out, protected)
    lock = _open(paths[3], os.O_WRONLY | os.O_CREAT, protected)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries, prev = _read_chain(out)
        del entries
        line = json.dumps({**record, "prev": prev}, sort_keys=True) + "\n"
        _write(out, line.encode(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, protected)
        tmp_fd = _open(paths[2], os.O_WRONLY | os.O_CREAT, protected, exclusive=True)
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write((hashlib.sha256(line.encode()).hexdigest() + "\n").encode())
        _check_head_target(paths[1], protected)
        os.replace(paths[2], paths[1])
    except OSError as exc:
        raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc
    finally:
        os.close(lock)


def _check_head_target(path: Path, protected: tuple[Path, ...]) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CasebookError("REPLAY_OUTPUT_REFUSED") from exc
    fd = _open(path, os.O_WRONLY, protected)
    os.close(fd)
