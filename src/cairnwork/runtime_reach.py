"""#1769: what the base collect run saw at runtime can refuse a test commit, never excuse one.

The recorder (fixture_record.py) lists the pytest plugin modules and the ``sys.path`` of a
collect-only run on the base. A static read cannot follow a plugin loaded by an import name or
registered at run time (#990, #1010), nor tell an archive that is on ``sys.path`` from one that is
only data (#967). This reads the record instead:

* a plugin module whose file is a base file the test commit changes (a ``conftest.py`` is left to
  conftest_check, which governs it) is refused;
* a plugin known only by its import name, whose dotted name is a base file the test commit
  changes, is refused;
* a ``sys.path`` entry that is a base file the test commit changes (an archive, whatever its
  extension) is refused. A directory that merely holds a changed file, or an archive that is only
  data, is not.

Only refusals come from here. No record, no checkout root or nothing changed leaves the verdict to
the static rules; a malformed entry is skipped. ``changed_on_base`` must hold paths the base
already has, so a file the test commit adds is never matched. The record is advisory (see
fixture_probe.py): a run that forges it can hide an entry, which is the limit already pinned there.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import PurePosixPath

from cairnwork.collected_names import CollectedRecord
from cairnwork.deferred_overlay import TestCommitRejected

CODE = "TEST_COMMIT_CONFTEST_STATIC"
_BYTECODE = (".pyc", ".pyo")


def check_runtime_reach(
    record: CollectedRecord | None, changed_on_base: Iterable[str], added: Iterable[str] = ()
) -> None:
    """Refuse when the record names a changed base file. ``added`` are the paths the test commit
    adds: only a ``sys.path`` entry may match one (an entry the base named but did not have, which
    the commit now supplies); a plugin file the base ran always exists on it."""
    changed = frozenset(changed_on_base)
    if record is None or not record.root or not changed:
        return
    on_path = changed | frozenset(added)
    for name, file in record.plugins:
        if (hit := _plugin_hit(record.root, name, file, changed)) is not None:
            _refuse(f"pytest loads {hit} as a plugin, and the test commit changes it")
    for entry in (*record.sys_path, *record.sys_path_finish):
        if (hit := _path_hit(record.root, entry, on_path)) is not None:
            _refuse(f"{hit} is on sys.path in the base run, and the test commit changes it")


def _refuse(why: str) -> None:
    raise TestCommitRejected(CODE, why)


def _plugin_hit(root: str, name: str, file: str | None, changed: frozenset[str]) -> str | None:
    if file is None:
        return _named(name, changed)
    for rel in _rels(root, file):
        if rel.lower().endswith(_BYTECODE):
            rel = rel[: rel.rindex(".")] + ".py"
        if rel in changed and PurePosixPath(rel).name != "conftest.py":
            return rel
    return None


def _path_hit(root: str, entry: str, changed: frozenset[str]) -> str | None:
    return next((rel for rel in _rels(root, entry) if rel in changed), None)


def _rels(root: str, path: str) -> tuple[str, ...]:
    """``path`` as repo-relative POSIX paths, as written and with symlinks resolved; none when it
    cannot be read or lies outside the checkout. A relative entry is read against the checkout."""
    try:
        full = path if os.path.isabs(path) else os.path.join(root, path)
        found = {os.path.normpath(full), os.path.realpath(full)}
        rels = (os.path.relpath(p, root) for p in found if _within(root, p))
        return tuple(sorted(r.replace(os.sep, "/") for r in rels if r != "."))
    except (ValueError, OSError):
        return ()


def _within(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _named(name: str, changed: frozenset[str]) -> str | None:
    """A changed file that the dotted ``name`` can import: ``a/b.py`` or ``a/b/__init__.py``
    under any leading directory, matched on whole path parts."""
    if not name or any(not part.isidentifier() for part in name.split(".")):
        return None
    tail = "/".join(name.split("."))
    for want in (f"{tail}.py", f"{tail}/__init__.py"):
        for path in sorted(changed):
            if path == want or path.endswith("/" + want):
                return None if PurePosixPath(path).name == "conftest.py" else path
    return None
