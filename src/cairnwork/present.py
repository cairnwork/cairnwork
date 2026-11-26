"""Is it there? Only "not found" means no (#1823).

pathlib's exists() / is_dir() / is_file() swallow every OSError on Python 3.14 (3.13 and older
raise PermissionError), so an unreadable or unsearchable owner path read as absent. These stat
first: FileNotFoundError and NotADirectoryError mean absent, any other OSError is raised.
"""

from __future__ import annotations

import os
import stat


def lookup(path, follow: bool = True) -> os.stat_result | None:
    """The stat of ``path`` (``follow`` False: the link itself), None only when it is not there."""
    try:
        return os.stat(path, follow_symlinks=follow)
    except (FileNotFoundError, NotADirectoryError):
        return None


def exists(path, follow: bool = True) -> bool:
    return lookup(path, follow) is not None


def is_dir(path, follow: bool = True) -> bool:
    info = lookup(path, follow)
    return info is not None and stat.S_ISDIR(info.st_mode)


def is_file(path, follow: bool = True) -> bool:
    info = lookup(path, follow)
    return info is not None and stat.S_ISREG(info.st_mode)
