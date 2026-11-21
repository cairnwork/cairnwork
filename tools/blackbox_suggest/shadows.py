"""Files on pytest's import path that take over a standard-library module name (#370).

pytest inserts the test directories (and the configured ``pythonpath``) at the front of
``sys.path``. A module or package there named like a standard-library module is then what an
``import`` of that name loads, in a test file or lazily inside pytest, so its top-level code runs
although the reader takes the import for the standard library's. Directories are only listed;
nothing is imported.
"""

from __future__ import annotations

import sys
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
            elif (path / "__init__.py").is_file():
                found.add(path.name)
    return found & STDLIB
