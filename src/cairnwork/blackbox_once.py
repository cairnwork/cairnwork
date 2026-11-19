"""Owner-side, atomic single-use claims for strict blackbox declarations."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from cairnwork.blackbox import Declaration
from cairnwork_protocol import canonical_hash


class AlreadyExposed(Exception):
    """The declaration has already been sent to a candidate process."""


def declaration_key(declaration: Declaration) -> str:
    """Hash the complete declaration, including answers but excluding its salt."""
    return canonical_hash(declaration._plain(with_expected=True))


def consume(root: Path, key: str) -> None:
    """Atomically claim a declaration in a private state directory."""
    root = Path(root)
    try:
        info = root.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and stat.S_ISLNK(info.st_mode):
        raise OSError("blackbox claim directory cannot be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(directory, 0o700)
        temporary = f".{key}.{secrets.token_hex(12)}.tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(b"used\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Linking publishes the claim without replacing a prior owner's record.
            os.link(temporary, f"{key}.used", src_dir_fd=directory, dst_dir_fd=directory)
        except FileExistsError as exc:
            raise AlreadyExposed from exc
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)
    finally:
        os.close(directory)
