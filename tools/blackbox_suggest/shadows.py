"""Files on pytest's import path that take over a standard-library module name (#370).

pytest inserts the test directories (and the configured ``pythonpath``) at the front of
``sys.path``. A module or package there named like a standard-library module is then what an
``import`` of that name loads, in a test file or lazily inside pytest, so its top-level code runs
although the reader takes the import for the standard library's. Directories are only listed;
nothing is imported.
"""

from __future__ import annotations

import os
import sys
from fnmatch import fnmatchcase
from importlib.machinery import all_suffixes
from pathlib import Path

STDLIB = set(sys.stdlib_module_names)
SUFFIXES = tuple(all_suffixes())


def stdlib_shadows(dirs: list[Path]) -> set[str]:
    """Standard-library names that a module file or a package in dirs would be imported as."""
    found: set[str] = set()
    for directory in dirs:
        try:
            entries = list(directory.iterdir()) if directory.is_dir() else []
        except OSError:
            entries = []
        for path in entries:
            if path.is_file() and path.name.endswith(SUFFIXES):
                found.add(path.name.split(".")[0])
            elif _is_package(path):
                found.add(path.name)
    return found & STDLIB


def base_dirs(root: Path, skips: tuple[str, ...]) -> list[Path] | None:
    """Every directory that prepend import mode may put on ``sys.path`` for some module under
    root: for each directory holding a Python file, the first one at or above it without an
    ``__init__``. Any file may be collected (``python_files``, arguments), so all count.
    Directories pytest does not collect from are not walked: names matching skips (its
    ``norecursedirs``), ``__pycache__`` and virtual environments. None when a directory cannot
    be listed or is a link (pytest follows links): then the reader cannot tell."""
    found: set[Path] = set()
    failed: list[OSError] = []
    for directory, subdirs, files in os.walk(root.resolve(), onerror=failed.append):
        here = Path(directory)
        subdirs[:] = [d for d in subdirs if not _skipped(here / d, skips)]
        if any((here / d).is_symlink() for d in subdirs):
            return None
        if any(name.endswith(SUFFIXES) for name in files):
            while _is_package(here) and here.parent != here:
                here = here.parent
            found.add(here)
    return None if failed else sorted(found)


def _skipped(path: Path, skips: tuple[str, ...]) -> bool:
    if path.name == "__pycache__" or (path / "pyvenv.cfg").is_file():
        return True
    return any(fnmatchcase(path.name, pattern) for pattern in skips)


def _is_package(path: Path) -> bool:
    return any((path / f"__init__{suffix}").is_file() for suffix in SUFFIXES)
