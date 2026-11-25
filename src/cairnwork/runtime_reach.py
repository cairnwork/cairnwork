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


UNRESOLVED = "\0unresolved"  # resolve_record could not tell where an entry leads


def check_runtime_reach(
    record: CollectedRecord | None,
    changed_on_base: Iterable[str],
    added: Iterable[str] = (),
    base_paths: Iterable[str] | None = None,
) -> None:
    """Refuse when the record names a changed base file. ``added`` are the paths the test commit
    adds: only a ``sys.path`` entry may match one (an entry the base named but did not have, which
    the commit now supplies), or a ``.py`` file below a loaded plugin package. ``base_paths`` is
    every file the base has: a name that differs from a changed file only by case is the same file
    unless the base holds the exact spelling (None: unknown, so the folded match stands)."""
    changed, added = frozenset(changed_on_base), frozenset(added)
    on_path = changed | added
    if record is None or not record.root or not on_path:
        return
    real, exact = dict(record.real), None if base_paths is None else frozenset(base_paths)
    for name, file in record.plugins:
        hit = _plugin_hit(record.root, name, file, changed, real, exact, added)
        if hit is not None:
            _refuse(f"pytest loads {hit} as a plugin, and the test commit {_verb(hit, added)} it")
    for entry in (*record.sys_path, *record.sys_path_finish):
        if (hit := _path_hit(record.root, entry, on_path, real, exact)) is not None:
            _refuse(
                f"{hit} is on sys.path in the base run, and the test commit {_verb(hit, added)} it"
            )


def resolve_record(record: CollectedRecord, checkout) -> CollectedRecord:
    """The record with its checkout root and the realpath of every entry and plugin file, read
    while the checkout still exists (a symlink cannot be followed once it is removed)."""
    root = _real(os.fspath(checkout)) or os.path.abspath(checkout)
    paths = (*record.sys_path, *record.sys_path_finish, *(f for _, f in record.plugins if f))
    real = {}
    for path in paths:
        try:
            real[path] = _real(_full(root, path)) or UNRESOLVED
        except ValueError:
            continue
    return replace(record, root=root, real=tuple(sorted(real.items())))


def _refuse(why: str) -> None:
    raise TestCommitRejected(CODE, why)


def _real(full: str) -> str | None:
    """realpath where only "not found" means absent (then the lexical result, as before); None
    for any other OSError (unsearchable parent, loop, name too long): 3.14 swallows them (#1823).
    A caller must not read None as absent: check_runtime_reach refuses it (_rels), because
    resolve_record runs inside a probe that drops its whole record on an exception."""
    try:
        return os.path.realpath(full, strict=True)
    except (FileNotFoundError, NotADirectoryError):
        return os.path.realpath(full)
    except OSError:
        return None


def _key(path: str) -> str:
    """Case and Unicode-normalisation folded, so a variant of a name is the same name (this can
    only refuse more)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", path).casefold())


def _verb(hit: str, added: frozenset[str]) -> str:
    return "adds" if hit in added else "changes"


def _same(rel: str, keys: dict[str, list[str]], exact: frozenset[str] | None) -> str | None:
    """The changed file ``rel`` names: the exact spelling, else a case or Unicode variant of it
    unless the base holds ``rel`` itself (then the variant is another file)."""
    if rel in keys.get(rel, ()):
        return rel
    if exact is not None and rel in exact:
        return None
    return next(iter(keys.get(_key(rel), ())), None)


def _by_key(paths: Iterable[str]) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for path in sorted(paths):
        keys.setdefault(path, []).append(path)
        keys.setdefault(_key(path), []).append(path)
    return keys


def _source_of(rel: str) -> str:
    """The ``.py`` a bytecode file was made from (``__pycache__/x.cpython-311.pyc`` -> ``x.py``)."""
    if not rel.lower().endswith(_BYTECODE):
        return rel
    path = PurePosixPath(rel)
    if path.parent.name == "__pycache__":
        return str(path.parent.parent / (path.name.split(".")[0] + ".py"))
    return rel[: rel.rindex(".")] + ".py"


def _plugin_hit(
    root: str,
    name: str,
    file: str | None,
    changed: frozenset[str],
    real: dict[str, str],
    exact: frozenset[str] | None = None,
    added: frozenset[str] = frozenset(),
) -> str | None:
    if file is None:
        return _named(name, changed | added, exact)
    keys = _by_key(changed)
    for rel in map(_source_of, _rels(root, file, real)):
        hit = _same(rel, keys, exact)
        if hit is not None and PurePosixPath(hit).name != "conftest.py":
            return hit
        package = PurePosixPath(rel)
        if package.name == "__init__.py":  # a package plugin: every .py below it is loaded code
            below = _below(str(package.parent), (*changed, *added), exact)
            if below is not None:
                return below
    return None


def _below(directory: str, paths: Iterable[str], exact: frozenset[str] | None) -> str | None:
    prefix = _key(directory) + "/"
    for path in sorted(paths):
        if path.endswith(".py") and PurePosixPath(path).name != "conftest.py":
            if _key(path).startswith(prefix) and (
                path.startswith(directory + "/") or exact is None or path not in exact
            ):
                return path
    return None


def _path_hit(
    root: str,
    entry: str,
    changed: frozenset[str],
    real: dict[str, str],
    exact: frozenset[str] | None = None,
) -> str | None:
    """A changed file that is the entry or any leading part of it (``lib.zip/pkg`` is inside
    ``lib.zip``)."""
    keys = _by_key(changed)
    for rel in _rels(root, entry, real):
        parts = PurePosixPath(rel).parts
        for end in range(1, len(parts) + 1):
            if (hit := _same("/".join(parts[:end]), keys, exact)) is not None:
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
        resolved = real.get(path) or _real(full) or UNRESOLVED
        if resolved == UNRESOLVED and _within(root, os.path.normpath(full)):
            _refuse("a sys.path or plugin entry of the base run cannot be resolved")
        found = {os.path.normpath(full), resolved}
        rels = (os.path.relpath(p, root) for p in found if _within(root, p))
        return tuple(sorted(r.replace(os.sep, "/") for r in rels if r != "."))
    except ValueError:
        return ()


def _within(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _named(name: str, changed: frozenset[str], exact: frozenset[str] | None = None) -> str | None:
    """A changed file that the dotted ``name`` can import: ``a/b.py`` or ``a/b/__init__.py``
    under any leading directory, matched on whole path parts."""
    if not name or any(not part.isidentifier() for part in name.split(".")):
        return None
    tail = "/".join(name.split("."))
    for want in (f"{tail}.py", f"{tail}/__init__.py"):
        for path in sorted(changed):
            if path == want or path.endswith("/" + want):
                return None if PurePosixPath(path).name == "conftest.py" else path
            folded = _key(path)
            if folded == _key(want) or folded.endswith("/" + _key(want)):
                spelled = path[: len(path) - len(want)] + want  # the exact spelling, in that place
                if exact is None or spelled not in exact:
                    return None if PurePosixPath(path).name == "conftest.py" else path
    return None
