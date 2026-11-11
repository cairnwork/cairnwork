"""Find nested module files that can shadow imports used by the candidate's tests (#1065)."""

from __future__ import annotations

import ast
import re
import sys
import warnings
from collections import Counter
from importlib.machinery import all_suffixes
from pathlib import Path

from cairnwork.gitcmd import GitCommandError, run_git_bytes

_OID = re.compile(r"^[0-9a-f]{40}$")
_IMPORT_CALLS = frozenset({"import_module", "__import__", "importorskip"})
_ALWAYS_SHADOW = frozenset({"pytest", "_pytest", "pluggy", "conftest", "py"})


def find_shadow_file(
    repo: Path,
    candidate: str,
    test_commit: str | None = None,
    changed: dict[str, str] | None = None,
) -> str | None:
    """Return the first nested file the candidate ``changed`` (path -> ADD/MODIFY) that can shadow
    an imported module; base content is trusted, so unchanged files never count.

    A changed nested file named like a standard or pytest module counts. A name some tree or test
    file imports (every dotted part, relative imports, import_module/__import__/importorskip
    strings, so a ``__path__`` redirect of ``pkg.helper`` is seen) counts when the file was added,
    another file has the same module name, or it is a modified unique module outside an import root.
    A modified module is already importable (not a shadow) if its directory is src or lib, contains
    an __init__.py, or shares a directory with an unchanged importer. Names compare
    case-insensitively; an unparseable ``.py`` the candidate changed fails closed, any other one
    imports nothing.

    Accepted false refusal: a modified, uniquely named module in a non-conventional import root
    (say app/foo.py made importable by a pytest pythonpath setting) that some other-directory test
    imports is refused, but only when a changed file also carries a redirect signal.

    Accepted residual: rule (c) exempts a modified module when any unchanged same-directory file
    imports the name, even if other tests rely on a redirect to bare-import it."""
    commits = tuple(
        dict.fromkeys((candidate,) if test_commit is None else (candidate, test_commit))
    )
    always = {name.casefold() for name in (*sys.stdlib_module_names, *_ALWAYS_SHADOW)}
    changed = changed or {}
    imported: set[str] = set()
    importer_dirs: dict[str, set[str]] = {}
    top_importers: dict[str, set[str]] = {}
    trees: dict[str, tuple[tuple[int, str, str, str], ...]] = {}
    for commit in commits:
        _validate_oid(commit)
        trees[commit] = _tree_entries(repo, commit)
    sources = [
        (commit, path, oid)
        for commit in commits
        for mode, typ, oid, path in trees[commit]
        if _is_source(path)
    ]
    blobs = _read_source_blobs(repo, sources)
    parsed: dict[str, set[str] | None] = {}
    parsed_top: dict[str, set[str]] = {}
    for commit, path, oid in sources:
        if oid not in parsed:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed[oid] = _parse_imports(blobs[oid], path)
                    parsed_top[oid] = _parse_top_level_imports(blobs[oid], path)
            except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError):
                parsed[oid] = None
                parsed_top[oid] = set()
        names = parsed[oid]
        if names is None and path in changed:
            raise ValueError(f"{commit}:{path} could not be parsed")
        dir_name = path.rpartition("/")[0]
        for raw_name in names or ():
            name = raw_name.casefold()
            imported.add(name)
            if path not in changed:
                importer_dirs.setdefault(name, set()).add(dir_name)
        for raw_top in parsed_top.get(oid, ()):
            top_importers.setdefault(raw_top.casefold(), set()).add(dir_name)
    paths = tuple(path for _, _, _, path in trees[candidate])
    counts = Counter(name for path in paths if (name := _module_name(path)))
    init_dirs = {
        p.rpartition("/")[0]
        for p in paths
        if p.rsplit("/", 1)[-1] == "__init__.py" and changed.get(p) != "ADD"
    }
    for path in sorted(changed):
        if _is_shadow(
            path, changed, always, imported, counts, init_dirs, importer_dirs, top_importers
        ):
            return path
    return None


def _is_shadow(
    path: str,
    changed: dict[str, str],
    always: set[str],
    imported: set[str],
    counts: Counter[str],
    init_dirs: set[str],
    importer_dirs: dict[str, set[str]],
    top_importers: dict[str, set[str]],
) -> bool:
    """Whether one changed path can shadow an imported module (#1065, #1148)."""
    depth = path.count("/") - (
        path.casefold().endswith("/__init__.py") or path.casefold() == "__init__.py"
    )
    if depth < 1 or not (module := _module_name(path)):  # root modules import as themselves
        return False
    added = changed[path] == "ADD"
    return bool(
        module in always
        or (
            module in imported
            and (
                added
                or counts[module] > 1
                or not _already_importable(path, module, init_dirs, importer_dirs, top_importers)
            )
        )
    )


def _already_importable(
    path: str,
    module: str,
    init_dirs: set[str],
    importer_dirs: dict[str, set[str]],
    top_importers: dict[str, set[str]],
) -> bool:
    """Whether a modified module was already importable without a path redirect (#1148)."""
    dir_path = path.rpartition("/")[0]
    is_init = path.rsplit("/", 1)[-1] == "__init__.py"
    pkg_dir = dir_path.rpartition("/")[0] if is_init else dir_path
    if pkg_dir in {"src", "lib"}:
        return True
    if pkg_dir in init_dirs and not (top_importers.get(module, set()) - {dir_path}):
        return True
    return dir_path in importer_dirs.get(module, ())


def _validate_oid(commit: str) -> None:
    if not _OID.fullmatch(commit):
        raise ValueError("commit must be a full 40-character object id")


def _tree_entries(repo: Path, commit: str) -> tuple[tuple[int, str, str, str], ...]:
    raw = run_git_bytes(repo, "ls-tree", "-r", "-z", commit)
    entries = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, typ, oid = header.split(b" ")
        entries.append(
            (
                int(mode, 8),
                typ.decode("ascii"),
                oid.decode("ascii"),
                path.decode("utf-8", "surrogateescape"),
            )
        )
    return tuple(entries)


def _read_source_blobs(repo: Path, sources: list[tuple[str, str, str]]) -> dict[str, bytes]:
    """Read each distinct source object in one batch; malformed or missing records fail closed."""
    oids = list(dict.fromkeys(oid for _, _, oid in sources))
    if not oids:
        return {}
    raw = run_git_bytes(
        repo, "cat-file", "--batch", stdin="".join(f"{oid}\n" for oid in oids).encode()
    )
    blobs: dict[str, bytes] = {}
    pos = 0
    for oid in oids:
        end = raw.find(b"\n", pos)
        header = raw[pos:end].split() if end >= 0 else []
        if (
            len(header) != 3
            or header[0] != oid.encode()
            or header[1] != b"blob"
            or not header[2].isdigit()
        ):
            raise GitCommandError("git cat-file did not return a blob")
        start, stop = end + 1, end + 1 + int(header[2])
        if stop >= len(raw) or raw[stop : stop + 1] != b"\n":
            raise GitCommandError("git cat-file returned a truncated blob")
        blobs[oid] = raw[start:stop]
        pos = stop + 1
    if pos != len(raw):
        raise GitCommandError("git cat-file returned unexpected data")
    return blobs


def _is_source(path: str) -> bool:
    return path.casefold().endswith(".py")


def _parse_imports(raw: bytes, path: str) -> set[str]:
    """Names one file imports; raises when it cannot be parsed (the caller treats that as None)."""
    tree = ast.parse(raw, filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(part for alias in node.names for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):  # relative too: from . import helper
            names.update((node.module or "").split("."))  # a submodule can be redirected
            names.update(alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, ast.Call) and _call_name(node.func) in _IMPORT_CALLS:
            named = (k.value for k in node.keywords if k.arg in {"name", "modname"})
            for arg in (*node.args[:1], *named):  # import_module("x.y"), __import__, importorskip
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.update(arg.value.lstrip(".").split("."))
    names.discard("")
    return names


def _parse_top_level_imports(raw: bytes, path: str) -> set[str]:
    """Top-level module names one file imports as a top-level module (#1148)."""
    tree = ast.parse(raw, filename=path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and _call_name(node.func) in _IMPORT_CALLS:
            named = (k.value for k in node.keywords if k.arg in {"name", "modname"})
            for arg in (*node.args[:1], *named):  # import_module("foo") is a top-level import
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if not arg.value.startswith("."):
                        names.add(arg.value.split(".", 1)[0])
    names.discard("")
    return names


def _call_name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _module_name(path: str) -> str | None:
    """The importable name a tree file provides: a module stem or a package directory."""
    name = path.rsplit("/", 1)[-1].casefold()  # compared case-insensitively: fail closed
    if name == "__init__.py":
        parts = path.casefold().split("/")
        return parts[-2] if len(parts) > 1 else None
    for suffix in sorted(all_suffixes(), key=len, reverse=True):  # .py, .pyc, extension modules
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None
