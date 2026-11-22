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
from importlib.machinery import PathFinder, all_suffixes
from pathlib import Path
from typing import NamedTuple

STDLIB = set(sys.stdlib_module_names)
SUFFIXES = tuple(all_suffixes())
DEFAULT_DOCTESTS = "test*.txt"  # the doctest plugin's glob when --doctest-glob is not given


def stdlib_shadows(dirs: list[Path]) -> set[str] | None:
    """Standard-library names that a module file or a package in dirs would be imported as.
    None when a directory cannot be read: then the reader cannot tell."""
    found: set[str] = set()
    try:
        for directory in dirs:
            for path in directory.iterdir() if directory.is_dir() else []:
                if path.name.split(".")[0] not in STDLIB:
                    continue  # only a standard-library name matters; nothing else is read
                if path.is_file() and path.name.endswith(SUFFIXES):
                    found.add(path.name.split(".")[0])
                elif _is_package(path):
                    found.add(path.name)
    except OSError:
        return None
    return found


def clashes(dirs: list[Path]) -> set[str] | None:
    """Module names that two places pytest may import from both define: two of dirs, or one of
    them and the installed environment. Which file an import loads then depends on the path
    order. ``conftest`` is pytest's own. None when a directory cannot be read."""
    places: dict[str, set[Path]] = {}
    try:
        for directory in {d.resolve() for d in dirs if d.is_dir()}:
            for path in directory.iterdir():
                name = _module_name(path)
                if name and name != "conftest":
                    places.setdefault(name, set()).add(directory)
    except OSError:
        return None
    seen = {d for found in places.values() for d in found}
    installed = [p for p in sys.path if p and Path(p).resolve() not in seen]
    return {n for n, found in places.items() if len(found) > 1 or _installed(n, installed)}


def _module_name(path: Path) -> str | None:
    """None also when the entry cannot be read: pytest cannot import it either."""
    try:
        if path.is_file() and path.name.endswith(SUFFIXES):
            return path.name.split(".")[0]
        return path.name if _is_package(path) else None
    except OSError:
        return None


def _installed(name: str, paths: list[str]) -> bool:
    """Whether the environment has a module or package by this name (found, not imported)."""
    try:
        spec = PathFinder.find_spec(name, paths)
    except (ImportError, ValueError, OSError):
        return True  # cannot tell
    return spec is not None and spec.origin is not None


class Walk(NamedTuple):
    base_dirs: list[Path]
    doctests: list[Path]


def walk(root: Path, skips: tuple[str, ...]) -> Walk | None:
    """What pytest may collect under root.

    ``base_dirs``: every directory that prepend import mode may put on ``sys.path`` for some
    module: for each directory holding a Python file, the first one at or above it without an
    ``__init__``. Any file may be collected (``python_files``, arguments), so all count.
    ``doctests``: text files the doctest plugin collects with no option set (``test*.txt``,
    in any case), whose examples run code the reader does not read (#370).
    Directories pytest does not collect from are not walked: names matching skips (its
    ``norecursedirs``), ``__pycache__`` and virtual environments. None when a directory cannot
    be listed or is a link (pytest follows links): then the reader cannot tell."""
    found: set[Path] = set()
    doctests: list[Path] = []
    failed: list[OSError] = []
    for directory, subdirs, files in os.walk(root.resolve(), onerror=failed.append):
        here = Path(directory)
        if not os.access(here, os.R_OK | os.X_OK):
            return None
        subdirs[:] = [d for d in subdirs if not _skipped(here / d, skips)]
        if any((here / d).is_symlink() for d in subdirs):
            return None
        doctests += [here / n for n in files if fnmatchcase(n.lower(), DEFAULT_DOCTESTS)]
        if any(name.endswith(SUFFIXES) for name in files):
            while _is_package(here) and here.parent != here:
                here = here.parent
            found.add(here)
    return None if failed else Walk(sorted(found), sorted(doctests))


def _skipped(path: Path, skips: tuple[str, ...]) -> bool:
    if path.name == "__pycache__" or any(fnmatchcase(path.name, p) for p in skips):
        return True
    try:
        return (path / "pyvenv.cfg").is_file()
    except OSError:  # Python < 3.14 raises for a directory it cannot read
        return False  # walked, so listing it fails and the reader refuses


def _is_package(path: Path) -> bool:
    return any((path / f"__init__{suffix}").is_file() for suffix in SUFFIXES)
