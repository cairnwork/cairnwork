"""Which ``private`` and ``private/run`` directories ``run_scrub`` may delete or seal (Issue #392).

Moved out of ``run_scrub`` unchanged. Nothing is deleted or chmod'd unless ``lstat`` shows a real
directory (never a symlink), its ``realpath`` is strictly inside the run root, and the private
directory's name is one ``mkdtemp`` makes (``<checkout>.<8 chars>``).
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

# tempfile.mkdtemp(prefix=f"{checkout.name}.") appends 8 chars from [a-z0-9_]. checkout.name
# ({run_id}-vN or {run_id}-vbb) has no dot, so a private dir has exactly one, before that suffix.
_PRIVATE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,160}\.[a-z0-9_]{8}$")


def _real_dir(path: Path) -> Path | None:
    """Resolved path if ``path`` itself is a directory. A symlink is None: never follow it."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return None
    return Path(os.path.realpath(path))


def _private_name(name: str) -> bool:
    return _PRIVATE_NAME.fullmatch(name) is not None


def run_tree_ok(private: Path, root: Path) -> bool:
    """True when ``private`` and ``private/run`` may be deleted.

    Both must be real directories (not symlinks), ``private`` a direct child of ``root`` by
    realpath, ``run`` a direct child of ``private``, and the name a private-dir name.
    """
    base, own = _real_dir(root), _real_dir(private)
    scratch = _real_dir(private / "run")
    if not (base and own and scratch and _private_name(private.name)):
        return False
    if scratch.name != "run" or own.name != private.name:
        return False
    return own.parent == base and scratch.parent == own


def seal_private(private: Path, root: Path) -> None:
    """chmod ``private`` to 0700 through a no-follow directory fd. A symlink is left alone."""
    base, own = _real_dir(root), _real_dir(private)
    if not (base and own and _private_name(private.name) and own.parent == base):
        return
    fd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
