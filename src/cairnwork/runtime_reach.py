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
import unicodedata
from collections.abc import Iterable
from dataclasses import replace
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
    changed, on_path = frozenset(changed_on_base), frozenset(changed_on_base) | frozenset(added)
    if record is None or not record.root or not on_path:
        return
    real = dict(record.real)
    for name, file in record.plugins:
        if (hit := _plugin_hit(record.root, name, file, changed, real)) is not None:
            _refuse(f"pytest loads {hit} as a plugin, and the test commit changes it")
    for entry in (*record.sys_path, *record.sys_path_finish):
        if (hit := _path_hit(record.root, entry, on_path, real)) is not None:
            _refuse(f"{hit} is on sys.path in the base run, and the test commit changes it")


def resolve_record(record: CollectedRecord, checkout) -> CollectedRecord:
    """The record with its checkout root and the realpath of every entry and plugin file, read
    while the checkout still exists (a symlink cannot be followed once it is removed)."""
    root = os.path.realpath(checkout)
    paths = (*record.sys_path, *record.sys_path_finish, *(f for _, f in record.plugins if f))
    real = {}
    for path in paths:
        try:
            real[path] = os.path.realpath(_full(root, path))
        except (ValueError, OSError):
            continue
    return replace(record, root=root, real=tuple(sorted(real.items())))


def _refuse(why: str) -> None:
    raise TestCommitRejected(CODE, why)


def _key(path: str) -> str:
    """Case and Unicode-normalisation folded, so a variant of a name is the same name (this can
    only refuse more)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def _plugin_hit(
    root: str, name: str, file: str | None, changed: frozenset[str], real: dict[str, str]
) -> str | None:
    if file is None:
        return _named(name, changed)
    keys = {_key(c): c for c in changed}
    for rel in _rels(root, file, real):
        if rel.lower().endswith(_BYTECODE):
            rel = rel[: rel.rindex(".")] + ".py"
        if (hit := keys.get(_key(rel))) is not None and PurePosixPath(hit).name != "conftest.py":
            return hit
    return None


def _path_hit(root: str, entry: str, changed: frozenset[str], real: dict[str, str]) -> str | None:
    """A changed file that is the entry or any leading part of it (``lib.zip/pkg`` is inside
    ``lib.zip``)."""
    keys = {_key(c): c for c in changed}
    for rel in _rels(root, entry, real):
        parts = PurePosixPath(rel).parts
        for end in range(1, len(parts) + 1):
            if (hit := keys.get(_key("/".join(parts[:end])))) is not None:
                return hit
    return None


def _full(root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(root, path)


def _rels(root: str, path: str, real: dict[str, str]) -> tuple[str, ...]:
    """``path`` as repo-relative POSIX paths, as written and with symlinks resolved (from the run
    when it was resolved there, else now); none when it cannot be read or lies outside the
    checkout. A relative entry is read against the checkout."""
    try:
        full = _full(root, path)
        found = {os.path.normpath(full), real.get(path) or os.path.realpath(full)}
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
