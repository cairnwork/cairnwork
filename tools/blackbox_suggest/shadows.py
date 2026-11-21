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
from functools import lru_cache
from importlib.machinery import all_suffixes
from pathlib import Path

STDLIB = set(sys.stdlib_module_names)
SUFFIXES = tuple(all_suffixes())
SKIPPED = {".git", "__pycache__", "node_modules"}


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


def base_dirs(root: Path) -> list[Path]:
    """Every directory that prepend import mode may put on ``sys.path`` for some module under
    root: for each directory holding a Python file, the first one at or above it without an
    ``__init__``. Any file may be collected (``python_files``, arguments), so all count.
    Virtual environments, ``.git``, ``node_modules`` and ``__pycache__`` are not walked."""
    return list(_base_dirs(root.resolve()))


@lru_cache(maxsize=16)
def _base_dirs(root: Path) -> tuple[Path, ...]:
    found: set[Path] = set()
    for directory, subdirs, files in os.walk(root):
        here = Path(directory)
        subdirs[:] = [
            d for d in subdirs if d not in SKIPPED and not (here / d / "pyvenv.cfg").is_file()
        ]
        if any(name.endswith(SUFFIXES) for name in files):
            while _is_package(here) and here.parent != here:
                here = here.parent
            found.add(here)
    return tuple(sorted(found))


def _is_package(path: Path) -> bool:
    return any((path / f"__init__{suffix}").is_file() for suffix in SUFFIXES)
